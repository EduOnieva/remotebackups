"""Fire scheduled runs from the cron expression in the configuration.

The expression is re-read every minute while waiting, so editing it in config.yaml moves
the next run without a restart. Sleeping happens in short chunks: if the host suspends past
a due time, the run fires once on wake instead of being lost or repeated.
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
        # The last configuration problem logged, so a broken file is reported once, not every minute
        self._config_problem: str | None = None

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
        pending: tuple[str, datetime] | None = None  # the cron expression and the due time it gave
        while True:
            cron = self._read_cron()
            if cron is None:
                pending = self._next = None
                await self._clock.sleep(IDLE_POLL_SECONDS)
                continue
            # Recomputed only when the expression changes: croniter looks strictly after "now",
            # so recomputing at the due instant itself would skip that run
            if pending is None or pending[0] != cron:
                pending = (cron, self._next_after(cron, self._clock.now()))
            due = self._next = pending[1]
            remaining = (due - self._clock.now()).total_seconds()
            if remaining > 0:
                await self._clock.sleep(min(remaining, CHUNK_SECONDS))
                continue
            await self._fire()
            pending = None

    def _read_cron(self) -> str | None:
        """The current expression, or None when scheduling is off or the file is broken."""
        try:
            cron = self._config_provider().schedule.cron
        except ConfigError as exc:
            if str(exc) != self._config_problem:
                logger.error(
                    "Scheduler cannot read the configuration, retrying every minute: %s", exc
                )
                self._config_problem = str(exc)
            return None
        if self._config_problem is not None:
            logger.info("Scheduler can read the configuration again")
            self._config_problem = None
        return cron

    def _next_after(self, cron: str, now: datetime) -> datetime:
        result: datetime = croniter(cron, now.astimezone(self._tz)).get_next(datetime)
        return result

    async def _fire(self) -> None:
        try:
            config = self._config_provider()
        except ConfigError as exc:
            logger.error("Scheduled run skipped, the configuration is invalid: %s", exc)
            return
        if config.schedule_paused:
            # Skipped, not deferred: resuming must never start a backup on the spot
            logger.warning("Scheduled run skipped: the schedule is paused from the UI")
            return
        try:
            timeout = config.schedule.rsync_timeout or None
            record = await self._engine.start(
                RunTrigger.SCHEDULED, RunOptions(rsync_timeout=timeout)
            )
        except RunAlreadyActiveError as exc:
            logger.warning("Scheduled run skipped: run %s is still active", exc.run_id)
        except ConfigError as exc:
            logger.error("Scheduled run skipped, the configuration is invalid: %s", exc)
        else:
            logger.info("Scheduled run %s started", record.id)
