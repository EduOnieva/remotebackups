"""Fire scheduled runs from the cron expression in the configuration.

The expression is re-read every time the next run is computed, so editing it in
config.yaml takes effect without a restart. Sleeping happens in short chunks: if the host
suspends past a due time, the run fires once on wake instead of being lost or repeated.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from croniter import croniter

from remotebackups.clock import Clock
from remotebackups.config import Config, ConfigError
from remotebackups.sync import RunAlreadyActiveError, RunOptions, RunRecord, RunTrigger

logger = logging.getLogger(__name__)

CHUNK_SECONDS = 60.0
IDLE_POLL_SECONDS = 60.0


class RunStarter(Protocol):
    async def start(self, trigger: RunTrigger, options: RunOptions) -> RunRecord: ...


class CronScheduler:
    def __init__(
        self,
        config_provider: Callable[[], Config],
        tz: ZoneInfo,
        engine: RunStarter,
        clock: Clock,
    ) -> None:
        self._config_provider = config_provider
        self._tz = tz
        self._engine = engine
        self._clock = clock
        self._next: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def next_run_at(self) -> datetime | None:
        """When the next scheduled run fires, in the configured timezone; None when disabled."""
        return self._next

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def run(self) -> None:
        while True:
            due = self._compute_next()
            self._next = due
            if due is None:
                await self._clock.sleep(IDLE_POLL_SECONDS)
                continue
            while (remaining := (due - self._clock.now()).total_seconds()) > 0:
                await self._clock.sleep(min(remaining, CHUNK_SECONDS))
            await self._fire()

    def _compute_next(self) -> datetime | None:
        try:
            cron = self._config_provider().schedule.cron
        except ConfigError as exc:
            logger.error("Scheduler cannot read the configuration, retrying later: %s", exc)
            return None
        if cron is None:
            return None
        now = self._clock.now().astimezone(self._tz)
        result: datetime = croniter(cron, now).get_next(datetime)
        return result

    async def _fire(self) -> None:
        try:
            timeout = self._config_provider().schedule.rsync_timeout or None
            record = await self._engine.start(
                RunTrigger.SCHEDULED, RunOptions(rsync_timeout=timeout)
            )
        except RunAlreadyActiveError as exc:
            logger.warning("Scheduled run skipped: run %s is still active", exc.run_id)
        except ConfigError as exc:
            logger.error("Scheduled run skipped, the configuration is invalid: %s", exc)
        else:
            logger.info("Scheduled run %s started", record.id)
