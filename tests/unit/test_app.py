import asyncio
import os
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient

from remotebackups.app import Application
from remotebackups.cli import main
from remotebackups.config import ConfigError, EnvSettings
from remotebackups.paths import ConfigPaths
from remotebackups.scheduler import CronScheduler
from remotebackups.sync import RunOptions, RunOutcome, RunTrigger
from tests.fakes.control import FakeBinaries

pytestmark = pytest.mark.usefixtures("fake_bin", "ssh_key")


@pytest.fixture
def configured(paths: ConfigPaths, tmp_path: Path) -> Path:
    source = tmp_path / "data" / "docs"
    source.mkdir(parents=True)
    paths.config_file.write_text(
        "remote: {host: 10.0.0.9, user: backup}\n"
        "schedule: {cron: null}\n"
        f"folders:\n  - source: {source}\n    destination: /mnt/backup/docs\n"
    )
    return source


def alive(scheduler: CronScheduler) -> bool:
    """Read through a call so mypy does not carry an earlier narrowing of the property."""
    return scheduler.alive


async def wait_for(condition: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_sigterm_stops_the_active_run_and_exits_zero(
    configured: Path, paths: ConfigPaths, fake_bin: FakeBinaries
) -> None:
    fake_bin.configure(rsync_mode="hang")
    app = Application(EnvSettings(), paths)
    task = asyncio.create_task(app.run())
    await wait_for(lambda: app.engine is not None and app.remote_status.ok is True)
    assert app.engine is not None and app.scheduler is not None
    assert alive(app.scheduler)
    assert app.scheduler.next_run_at is None, "cron is null in this config"

    await app.engine.start(RunTrigger.MANUAL, RunOptions())
    await wait_for(
        lambda: (
            app.engine is not None
            and app.engine.current is not None
            and app.engine.current.current_folder is not None
        )
    )
    await asyncio.sleep(0.3)
    os.kill(os.getpid(), signal.SIGTERM)

    assert await task == 0
    record = app.engine.last
    assert record is not None
    assert record.outcome is RunOutcome.CANCELLED
    assert record.reason == "shutdown"
    assert not alive(app.scheduler)
    assert app.engine.current is None


async def test_web_server_answers_health_and_status(
    configured: Path, paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEB_PORT", "0")  # let the OS pick a free port
    app = Application(EnvSettings(), paths)
    task = asyncio.create_task(app.run())
    await wait_for(lambda: app.web_port is not None and app.remote_status.ok is not None)

    async with AsyncClient(base_url=f"http://127.0.0.1:{app.web_port}") as http:
        health = await http.get("/healthz")
        status = await http.get("/api/status")
    assert health.status_code == 200
    assert status.json()["remote"]["ok"] is True
    assert status.json()["schedule"] is None

    monkeypatch.setenv("WEB_PORT", str(app.web_port))
    assert await asyncio.to_thread(main, ["healthcheck"]) == 0
    monkeypatch.setenv("WEB_PORT", "1")  # nothing listens there
    assert await asyncio.to_thread(main, ["healthcheck"]) == 1

    app.request_shutdown("test")
    assert await task == 0


async def test_remote_problems_are_reported_not_fatal(
    configured: Path, paths: ConfigPaths, fake_bin: FakeBinaries
) -> None:
    fake_bin.configure(ssh_mode="refused")
    app = Application(EnvSettings(), paths)
    task = asyncio.create_task(app.run())
    await wait_for(lambda: app.remote_status.ok is not None)

    assert app.remote_status.ok is False
    assert "Connection refused" in app.remote_status.message
    assert app.remote_status.checked_at is not None
    app.request_shutdown("test")
    assert await task == 0


async def test_missing_credentials_show_up_in_the_remote_status(
    configured: Path, paths: ConfigPaths, ssh_key: Path
) -> None:
    ssh_key.unlink()
    app = Application(EnvSettings(), paths)
    task = asyncio.create_task(app.run())
    await wait_for(lambda: app.remote_status.ok is not None)

    assert app.remote_status.ok is False
    assert "No SSH key" in app.remote_status.message
    app.request_shutdown("test")
    assert await task == 0


async def test_invalid_config_raises_before_serving(paths: ConfigPaths) -> None:
    paths.config_file.write_text("folders:\n  - source: relative\n    destination: /x\n")
    with pytest.raises(ConfigError, match="source must be an absolute path"):
        await Application(EnvSettings(), paths).run()


async def test_crashed_run_is_closed_at_startup(
    configured: Path, paths: ConfigPaths, fake_bin: FakeBinaries
) -> None:
    # Started an hour ago, so it is well inside the retention window and survives prune()
    started = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (paths.runs_dir / "20260101T000000Z-dead.json").write_text(
        '{"id": "20260101T000000Z-dead", "trigger": "manual", "delete": false, '
        f'"started_at": "{started}", "outcome": "running", "folders": []}}'
    )
    (paths.runs_dir / "20260101T000000Z-dead.log").write_text("started\n")
    app = Application(EnvSettings(), paths)
    task = asyncio.create_task(app.run())
    await wait_for(lambda: app.history is not None)
    app.request_shutdown("test")
    await task

    assert app.history is not None
    record = app.history.get("20260101T000000Z-dead")
    assert record is not None and record.outcome is RunOutcome.ABORTED
