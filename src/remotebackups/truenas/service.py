"""Cached, non-blocking access to the TrueNAS status for the web layer."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from remotebackups.clock import Clock
from remotebackups.config import Config
from remotebackups.truenas.client import PoolStatus, TrueNASClient
from remotebackups.truenas.models import AlertInfo, PoolInfo

logger = logging.getLogger(__name__)

DISABLED: dict[str, int | str] = {"truenas": "disabled"}


class TrueNASDetails(BaseModel):
    """What /api/truenas returns: the parsed pools and alerts, or why there are none."""

    enabled: bool
    fetched_at: datetime | None = None
    error: str | None = None
    pools: list[PoolInfo] = Field(default_factory=list)
    alerts: list[AlertInfo] = Field(default_factory=list)


class TrueNASService:
    """One query at a time, answers reused for `truenas.cache_seconds`, warnings de-duplicated."""

    def __init__(self, config_provider: Callable[[], Config], clock: Clock) -> None:
        self._config_provider = config_provider
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cached: PoolStatus | None = None
        self._cached_at: datetime | None = None
        self._last_error: str | None = None
        self._last_alerts: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self._config_provider().truenas.is_enabled

    async def pool_status(self) -> dict[str, int | str]:
        """The v1-compatible shape for dashboards."""
        status = await self._fetch()
        return dict(status.result) if status is not None else dict(DISABLED)

    async def details(self) -> TrueNASDetails:
        """The parsed pools and alerts for the UI, from the same cached query."""
        status = await self._fetch()
        if status is None:
            return TrueNASDetails(enabled=False)
        error = status.result.get("error")
        return TrueNASDetails(
            enabled=True,
            fetched_at=self._cached_at,
            error=str(error) if error is not None else None,
            pools=list(status.pools),
            alerts=list(status.alerts),
        )

    async def _fetch(self) -> PoolStatus | None:
        """None when disabled; otherwise the cached or freshly queried status."""
        config = self._config_provider()
        truenas = config.truenas
        if not truenas.is_enabled or truenas.api_key is None:
            return None
        async with self._lock:
            now = self._clock.now()
            if (
                self._cached is not None
                and self._cached_at is not None
                and now - self._cached_at < timedelta(seconds=truenas.cache_seconds)
            ):
                return self._cached
            client = TrueNASClient(
                truenas.host or config.remote.host or "",
                truenas.api_key.get_secret_value(),
                verify_tls=truenas.verify_tls,
                pool_aliases=truenas.pool_aliases,
            )
            status = await asyncio.to_thread(client.query)
            self._log_transition(status)
            self._cached, self._cached_at = status, now
            return status

    def _log_transition(self, status: PoolStatus) -> None:
        """Log errors and alerts when they change, not on every poll of the UI."""
        error = status.result.get("error")
        if isinstance(error, str):
            if error != self._last_error:
                logger.warning("TrueNAS status unavailable: %s", error)
            self._last_error = error
            return
        if self._last_error is not None:
            logger.info("TrueNAS status available again")
            self._last_error = None
        if status.alert_texts != self._last_alerts:
            if status.alert_texts:
                logger.warning(
                    "TrueNAS alerts (%d): %s",
                    len(status.alert_texts),
                    "; ".join(status.alert_texts),
                )
            else:
                logger.info("TrueNAS alerts cleared")
            self._last_alerts = status.alert_texts
