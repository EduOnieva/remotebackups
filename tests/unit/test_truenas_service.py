import asyncio
import logging
from collections.abc import Callable
from unittest.mock import patch

import pytest

from remotebackups.config import Config
from remotebackups.truenas.client import PoolStatus
from remotebackups.truenas.models import AlertInfo, PoolInfo
from remotebackups.truenas.service import DISABLED, TrueNASService
from tests.fakes.clock import FakeClock


def service(config: Config, clock: FakeClock) -> TrueNASService:
    return TrueNASService(lambda: config, clock)


async def test_disabled_without_key(clock: FakeClock, make_config: Callable[..., Config]) -> None:
    svc = service(make_config(), clock)
    assert svc.enabled is False
    assert await svc.pool_status() == DISABLED


async def test_enabled_by_key_queries_the_nas_in_a_thread(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(truenas={"api_key": "1-abc", "pool_aliases": {"tank": "disk-0"}})
    svc = service(config, clock)
    assert svc.enabled is True

    with patch(
        "remotebackups.truenas.service.TrueNASClient.query",
        return_value=PoolStatus({"alerts": 0, "disk-0": "3.42 TB"}),
    ) as query:
        assert await svc.pool_status() == {"alerts": 0, "disk-0": "3.42 TB"}
    assert query.call_count == 1


async def test_results_are_cached_for_cache_seconds(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(truenas={"api_key": "1-abc", "cache_seconds": 30})
    svc = service(config, clock)
    with patch(
        "remotebackups.truenas.service.TrueNASClient.query",
        return_value=PoolStatus({"alerts": 0}),
    ) as query:
        await svc.pool_status()
        clock.advance(29)
        await svc.pool_status()
        assert query.call_count == 1
        clock.advance(2)
        await svc.pool_status()
        assert query.call_count == 2


async def test_concurrent_callers_share_one_query(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(truenas={"api_key": "1-abc"})
    svc = service(config, clock)
    with patch(
        "remotebackups.truenas.service.TrueNASClient.query",
        return_value=PoolStatus({"alerts": 2}),
    ) as query:
        results = await asyncio.gather(*(svc.pool_status() for _ in range(5)))
    assert results == [{"alerts": 2}] * 5
    assert query.call_count == 1


async def test_errors_are_logged_once_per_change_and_recovery_once(
    clock: FakeClock, make_config: Callable[..., Config], caplog: pytest.LogCaptureFixture
) -> None:
    config = make_config(truenas={"api_key": "1-abc", "cache_seconds": 0})
    svc = service(config, clock)
    answers = iter(
        [
            PoolStatus({"error": "Failed to connect to TrueNAS: unreachable"}),
            PoolStatus({"error": "Failed to connect to TrueNAS: unreachable"}),
            PoolStatus({"error": "Failed to connect to TrueNAS: refused"}),
            PoolStatus({"alerts": 0}),
        ]
    )
    with (
        patch(
            "remotebackups.truenas.service.TrueNASClient.query",
            side_effect=lambda: next(answers),
        ),
        caplog.at_level(logging.INFO, logger="remotebackups.truenas.service"),
    ):
        for _ in range(4):
            await svc.pool_status()
            clock.advance(1)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        "TrueNAS status unavailable: Failed to connect to TrueNAS: unreachable",
        "TrueNAS status unavailable: Failed to connect to TrueNAS: refused",
    ]
    assert "TrueNAS status available again" in caplog.text


async def test_alerts_are_logged_only_when_they_change(
    clock: FakeClock, make_config: Callable[..., Config], caplog: pytest.LogCaptureFixture
) -> None:
    config = make_config(truenas={"api_key": "1-abc", "cache_seconds": 0})
    svc = service(config, clock)
    answers = iter(
        [
            PoolStatus({"alerts": 1}, ("Updates are available for 1 application: tailscale",)),
            PoolStatus({"alerts": 1}, ("Updates are available for 1 application: tailscale",)),
            PoolStatus(
                {"alerts": 2},
                (
                    "Updates are available for 1 application: tailscale",
                    "Pool backup-disk-1 state is DEGRADED",
                ),
            ),
            PoolStatus({"alerts": 0}),
            PoolStatus({"alerts": 0}),
        ]
    )
    with (
        patch(
            "remotebackups.truenas.service.TrueNASClient.query",
            side_effect=lambda: next(answers),
        ),
        caplog.at_level(logging.INFO, logger="remotebackups.truenas.service"),
    ):
        for _ in range(5):
            await svc.pool_status()
            clock.advance(1)

    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "TrueNAS alerts (1): Updates are available for 1 application: tailscale",
        "TrueNAS alerts (2): Updates are available for 1 application: tailscale; "
        "Pool backup-disk-1 state is DEGRADED",
        "TrueNAS alerts cleared",
    ]


async def test_details_come_from_the_same_cached_query(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(truenas={"api_key": "1-abc", "cache_seconds": 30})
    svc = service(config, clock)
    pool = PoolInfo(
        name="tank", alias="disk-0", status="ONLINE", healthy=True, size=10, allocated=4, free=6
    )
    alert = AlertInfo(level="WARNING", text="Scrub overdue")
    with patch(
        "remotebackups.truenas.service.TrueNASClient.query",
        return_value=PoolStatus(
            {"alerts": 1, "disk-0": "0.0 TB"}, ("Scrub overdue",), (pool,), (alert,)
        ),
    ) as query:
        details = await svc.details()
        compat = await svc.pool_status()

    assert query.call_count == 1, "details and pool_status share the cache"
    assert details.enabled is True
    assert details.fetched_at == clock.now()
    assert details.error is None
    assert details.pools == [pool]
    assert details.alerts == [alert]
    assert compat == {"alerts": 1, "disk-0": "0.0 TB"}


async def test_details_when_disabled_and_when_unreachable(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    assert (await service(make_config(), clock).details()).enabled is False

    svc = service(make_config(truenas={"api_key": "1-abc"}), clock)
    with patch(
        "remotebackups.truenas.service.TrueNASClient.query",
        return_value=PoolStatus({"error": "Failed to connect to TrueNAS: unreachable"}),
    ):
        details = await svc.details()
    assert details.enabled is True
    assert details.error == "Failed to connect to TrueNAS: unreachable"
    assert details.pools == []
