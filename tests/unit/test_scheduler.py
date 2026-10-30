import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from remotebackups.config import Config, ConfigError
from remotebackups.scheduler import CronScheduler
from remotebackups.sync import RunAlreadyActiveError, RunOptions, RunRecord, RunTrigger
from tests.fakes.clock import FakeClock

MADRID = ZoneInfo("Europe/Madrid")


class FakeEngine:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: list[tuple[RunTrigger, RunOptions]] = []
        self.fired_at: list[datetime] = []
        self.busy_with: str | None = None

    async def start(self, trigger: RunTrigger, options: RunOptions) -> RunRecord:
        if self.busy_with:
            raise RunAlreadyActiveError(self.busy_with)
        self.calls.append((trigger, options))
        self.fired_at.append(self._clock.now())
        return RunRecord(
            id=f"run-{len(self.calls)}",
            trigger=trigger,
            delete=options.delete,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def provider_for(config: Config) -> Callable[[], Config]:
    return lambda: config


async def run_until(scheduler: CronScheduler, condition: Callable[[], bool]) -> None:
    scheduler.start()
    try:
        async with asyncio.timeout(5):
            while not condition():
                await asyncio.sleep(0.001)
    finally:
        await scheduler.stop()


async def test_next_run_is_computed_in_the_configured_timezone(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(schedule={"cron": "30 1 * * *"})
    engine = FakeEngine(clock)
    clock.limit = clock.now()  # do not let time move yet

    scheduler = CronScheduler(provider_for(config), MADRID, engine, clock)
    await run_until(scheduler, lambda: scheduler.next_run_at is not None)

    assert scheduler.next_run_at == datetime(2026, 10, 30, 1, 30, tzinfo=MADRID)
    assert scheduler.next_run_at.astimezone(UTC) == datetime(2026, 10, 30, 0, 30, tzinfo=UTC)
    assert engine.calls == []


async def test_fires_once_when_due_with_the_scheduled_timeout(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(schedule={"cron": "30 1 * * *", "rsync_timeout": 1800})
    engine = FakeEngine(clock)
    due = datetime(2026, 10, 30, 0, 30, tzinfo=UTC)
    clock.limit = due + timedelta(seconds=1)

    scheduler = CronScheduler(provider_for(config), MADRID, engine, clock)
    await run_until(scheduler, lambda: len(engine.calls) == 1)

    trigger, options = engine.calls[0]
    assert trigger is RunTrigger.SCHEDULED
    assert options == RunOptions(delete=False, rsync_timeout=1800)
    assert engine.fired_at == [due]
    assert scheduler.next_run_at == datetime(2026, 10, 31, 1, 30, tzinfo=MADRID)
    assert all(chunk <= 60 for chunk in clock.sleeps), "sleeps are chunked so suspend is noticed"


async def test_zero_timeout_means_no_timeout(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(schedule={"cron": "30 1 * * *", "rsync_timeout": 0})
    engine = FakeEngine(clock)
    clock.limit = datetime(2026, 10, 30, 0, 31, tzinfo=UTC)

    scheduler = CronScheduler(provider_for(config), MADRID, engine, clock)
    await run_until(scheduler, lambda: len(engine.calls) == 1)
    assert engine.calls[0][1].rsync_timeout is None


async def test_missed_tick_after_a_long_sleep_fires_once(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(schedule={"cron": "30 1 * * *"})
    engine = FakeEngine(clock)
    clock.limit = clock.now()
    scheduler = CronScheduler(provider_for(config), MADRID, engine, clock)
    await run_until(scheduler, lambda: scheduler.next_run_at is not None)

    # The host was suspended for three days: several 01:30s went by unseen
    clock.limit = None
    clock.advance(3 * 24 * 3600)
    await run_until(scheduler, lambda: len(engine.calls) == 1)
    await asyncio.sleep(0.01)

    assert len(engine.calls) == 1
    assert scheduler.next_run_at is not None
    assert scheduler.next_run_at > clock.now()


async def test_paused_schedule_skips_the_due_run_and_moves_on(
    clock: FakeClock, make_config: Callable[..., Config], caplog: pytest.LogCaptureFixture
) -> None:
    config = make_config(schedule={"cron": "30 1 * * *"})
    config.set_schedule_paused(True)
    engine = FakeEngine(clock)
    clock.limit = datetime(2026, 10, 30, 0, 31, tzinfo=UTC)
    scheduler = CronScheduler(provider_for(config), MADRID, engine, clock)
    with caplog.at_level(logging.WARNING, logger="remotebackups.scheduler"):
        await run_until(
            scheduler, lambda: scheduler.next_run_at == datetime(2026, 10, 31, 1, 30, tzinfo=MADRID)
        )

    assert engine.calls == [], "skipped, not deferred"
    assert "Scheduled run skipped: the schedule is paused from the UI" in caplog.text

    # Resuming fires nothing by itself; the next occurrence runs as usual
    config.set_schedule_paused(False)
    await asyncio.sleep(0.01)
    assert engine.calls == []
    clock.limit = datetime(2026, 10, 31, 0, 31, tzinfo=UTC)
    await run_until(scheduler, lambda: len(engine.calls) == 1)
    assert engine.fired_at == [datetime(2026, 10, 31, 0, 30, tzinfo=UTC)]


async def test_busy_engine_skips_with_a_warning(
    clock: FakeClock, make_config: Callable[..., Config], caplog: pytest.LogCaptureFixture
) -> None:
    config = make_config(schedule={"cron": "30 1 * * *"})
    engine = FakeEngine(clock)
    engine.busy_with = "run-manual"
    clock.limit = datetime(2026, 10, 30, 0, 31, tzinfo=UTC)

    scheduler = CronScheduler(provider_for(config), MADRID, engine, clock)
    with caplog.at_level(logging.WARNING, logger="remotebackups.scheduler"):
        await run_until(scheduler, lambda: "Scheduled run skipped" in caplog.text)

    assert "run run-manual is still active" in caplog.text
    assert engine.calls == []
    assert scheduler.next_run_at == datetime(2026, 10, 31, 1, 30, tzinfo=MADRID)


async def test_disabled_schedule_has_no_next_run_and_never_fires(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config(schedule={"cron": None})
    engine = FakeEngine(clock)
    deadline = clock.now() + timedelta(days=30)
    clock.limit = deadline

    scheduler = CronScheduler(provider_for(config), MADRID, engine, clock)
    await run_until(scheduler, lambda: clock.now() >= deadline)

    assert scheduler.next_run_at is None
    assert engine.calls == []


async def test_schedule_changes_are_picked_up_without_restart(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    configs = [make_config(schedule={"cron": "30 1 * * *"})]
    engine = FakeEngine(clock)
    clock.limit = datetime(2026, 10, 30, 0, 31, tzinfo=UTC)
    scheduler = CronScheduler(lambda: configs[-1], MADRID, engine, clock)
    await run_until(scheduler, lambda: len(engine.calls) == 1)

    configs.append(make_config(schedule={"cron": "0 12 * * *"}))
    clock.limit = datetime(2026, 10, 30, 11, 1, tzinfo=UTC)
    await run_until(scheduler, lambda: len(engine.calls) == 2)

    assert engine.fired_at[1] == datetime(2026, 10, 30, 11, 0, tzinfo=UTC), "12:00 CET"


async def test_cron_edited_while_waiting_moves_the_next_run(
    clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    configs = [make_config(schedule={"cron": "30 1 * * *"})]
    engine = FakeEngine(clock)
    clock.limit = clock.now()
    scheduler = CronScheduler(lambda: configs[-1], MADRID, engine, clock)
    await run_until(scheduler, lambda: scheduler.next_run_at is not None)
    assert scheduler.next_run_at == datetime(2026, 10, 30, 1, 30, tzinfo=MADRID)

    # The file now says 14:00 today, well before the 01:30 the scheduler was waiting for
    configs.append(make_config(schedule={"cron": "0 14 * * *"}))
    clock.limit = datetime(2026, 10, 29, 12, 2, tzinfo=UTC)
    await run_until(
        scheduler, lambda: scheduler.next_run_at == datetime(2026, 10, 29, 14, 0, tzinfo=MADRID)
    )

    clock.limit = datetime(2026, 10, 29, 13, 1, tzinfo=UTC)
    await run_until(scheduler, lambda: len(engine.calls) == 1)
    assert engine.fired_at == [datetime(2026, 10, 29, 13, 0, tzinfo=UTC)], "14:00 CET"


async def test_invalid_configuration_is_logged_once_and_retried(
    clock: FakeClock, make_config: Callable[..., Config], caplog: pytest.LogCaptureFixture
) -> None:
    broken = True

    def provider() -> Config:
        if broken:
            raise ConfigError(["folders[0].source: source must be an absolute path"])
        return make_config(schedule={"cron": "30 1 * * *"})

    engine = FakeEngine(clock)
    deadline = clock.now() + timedelta(minutes=5)
    clock.limit = deadline
    scheduler = CronScheduler(provider, MADRID, engine, clock)
    with caplog.at_level(logging.INFO, logger="remotebackups.scheduler"):
        await run_until(scheduler, lambda: clock.now() >= deadline)
        assert scheduler.next_run_at is None
        assert engine.calls == []

        broken = False
        clock.limit = deadline + timedelta(minutes=2)
        await run_until(scheduler, lambda: scheduler.next_run_at is not None)

    assert caplog.text.count("Scheduler cannot read the configuration") == 1, "five polls, one line"
    assert "Scheduler can read the configuration again" in caplog.text
