"""Cached, non-blocking access to the TrueNAS status for the web layer."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from remotebackups.clock import Clock
from remotebackups.config import Config
from remotebackups.truenas.client import TrueNASClient

logger = logging.getLogger(__name__)

DISABLED: dict[str, int | str] = {"truenas": "disabled"}


class TrueNASService:
    """One query at a time, answers reused for `truenas.cache_seconds`, warnings de-duplicated."""

    def __init__(self, config_provider: Callable[[], Config], clock: Clock) -> None:
        self._config_provider = config_provider
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cached: dict[str, int | str] | None = None
        self._cached_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._config_provider().truenas.is_enabled

    async def pool_status(self) -> dict[str, int | str]:
        config = self._config_provider()
        truenas = config.truenas
        if not truenas.is_enabled or truenas.api_key is None:
            return dict(DISABLED)
        async with self._lock:
            now = self._clock.now()
            if (
                self._cached is not None
                and self._cached_at is not None
                and now - self._cached_at < timedelta(seconds=truenas.cache_seconds)
            ):
                return dict(self._cached)
            client = TrueNASClient(
                truenas.host or config.remote.host or "",
                truenas.api_key.get_secret_value(),
                verify_tls=truenas.verify_tls,
                pool_aliases=truenas.pool_aliases,
            )
            result = await asyncio.to_thread(client.get_pool_status)
            self._log_transition(result)
            self._cached, self._cached_at = result, now
            return dict(result)

    def _log_transition(self, result: dict[str, int | str]) -> None:
        error = result.get("error")
        if isinstance(error, str):
            if error != self._last_error:
                logger.warning("TrueNAS status unavailable: %s", error)
            self._last_error = error
        elif self._last_error is not None:
            logger.info("TrueNAS status available again")
            self._last_error = None
