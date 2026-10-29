import asyncio
import fcntl
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from remotebackups.config.schema import Config
from remotebackups.paths import ConfigPaths
from remotebackups.sync import (
    FolderStatus,
    RunAlreadyActiveError,
    RunHistory,
    RunOptions,
    RunOutcome,
    RunState,
    RunTrigger,
    SyncEngine,
    UnknownFolderError,
)
from tests.fakes.clock import FakeClock
from tests.fakes.control import FakeBinaries

pytestmark = pytest.mark.usefixtures("fake_bin", "ssh_key")


@pytest.fixture
def history(paths: ConfigPaths, clock: FakeClock) -> RunHistory:
    return RunHistory(paths.runs_dir, keep_days=40, clock=clock)


@pytest.fixture
def engine(
    paths: ConfigPaths, history: RunHistory, clock: FakeClock, make_config: Callable[..., Config]
) -> SyncEngine:
    config = make_config()
    return SyncEngine(lambda: config, paths, history, clock, grace_seconds=2)


async def test_successful_run(
    engine: SyncEngine,
    history: RunHistory,
    paths: ConfigPaths,
    fake_bin: FakeBinaries,
    tmp_path: Path,
) -> None:
    args_file = tmp_path / "rsync-args"
    fake_bin.configure(rsync_args_file=str(args_file))

    started = await engine.start(RunTrigger.MANUAL, RunOptions())
    assert state_of(engine) is RunState.RUNNING
    assert engine.current is started
    record = await engine.wait()

    assert record is not None
    assert record.outcome is RunOutcome.COMPLETED
    assert [f.status for f in record.folders] == [FolderStatus.OK, FolderStatus.OK]
    assert record.files_transferred == 6
    assert record.bytes_sent == 25_000
    assert record.duration_s == 0
    assert state_of(engine) is RunState.IDLE
    assert history.get(record.id) == record

    log = history.log_path(record.id).read_text()
    assert "[INFO] Folder docs:" in log
    assert "sending incremental file list" in log
    assert "outcome=completed ok=2 failed=0" in log

    calls = [json.loads(line) for line in args_file.read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0][-1] == "backup@10.0.0.9:/mnt/backup/docs/"
    assert "--no-owner" in calls[0]
    assert "--delete" not in calls[0]
    assert not any(a.startswith("--timeout") for a in calls[0])


async def test_scheduled_run_passes_delete_and_timeout(
    engine: SyncEngine, fake_bin: FakeBinaries, tmp_path: Path
) -> None:
    args_file = tmp_path / "rsync-args"
    fake_bin.configure(rsync_args_file=str(args_file))

    await engine.start(RunTrigger.SCHEDULED, RunOptions(delete=True, rsync_timeout=3600))
    record = await engine.wait()

    assert record is not None and record.trigger is RunTrigger.SCHEDULED and record.delete
    argv = json.loads(args_file.read_text().splitlines()[0])
    assert "--delete" in argv
    assert "--timeout=3600" in argv


async def test_partial_failure_is_completed_with_errors(
    engine: SyncEngine, history: RunHistory, fake_bin: FakeBinaries
) -> None:
    fake_bin.configure(rsync_mode="exit:23")
    await engine.start(RunTrigger.MANUAL, RunOptions(folders=["docs"]))
    record = await engine.wait()

    assert record is not None
    assert record.outcome is RunOutcome.FAILED, "every selected folder failed"
    docs = record.folders[0]
    assert docs.status is FolderStatus.FAILED
    assert docs.exit_code == 23
    assert (
        docs.message
        == "rsync exit 23: partial transfer due to error (see the rsync messages above)"
    )
    assert docs.stats is not None and docs.stats.files_transferred == 3
    assert "[ERROR] Folder docs: rsync exit 23" in history.log_path(record.id).read_text()


async def test_one_failed_folder_out_of_two(
    paths: ConfigPaths, history: RunHistory, clock: FakeClock, make_config: Callable[..., Config]
) -> None:
    config = make_config()
    Path(config.folders[1].source).rmdir()  # photos no longer exists inside the "container"
    engine = SyncEngine(lambda: config, paths, history, clock)

    await engine.start(RunTrigger.MANUAL, RunOptions())
    record = await engine.wait()

    assert record is not None
    assert record.outcome is RunOutcome.COMPLETED_WITH_ERRORS
    assert record.folders[1].status is FolderStatus.FAILED
    assert record.folders[1].message == "source directory not found inside the container"
    assert record.folders_failed == 1


async def test_cancel_stops_the_current_folder_and_skips_the_rest(
    engine: SyncEngine, history: RunHistory, fake_bin: FakeBinaries
) -> None:
    fake_bin.configure(rsync_mode="hang")
    await engine.start(RunTrigger.MANUAL, RunOptions())
    await _wait_for(
        lambda: engine.current is not None and engine.current.current_folder is not None
    )
    await asyncio.sleep(0.3)  # let the fake print something

    record = await engine.cancel("user")
    assert state_of(engine) is RunState.IDLE, "cancel returns once the run has been closed"

    assert record is not None
    assert record.outcome is RunOutcome.CANCELLED
    assert record.reason == "user"
    assert record.folders[0].status is FolderStatus.FAILED
    assert record.folders[0].exit_code == 20
    assert record.folders[1].status is FolderStatus.SKIPPED
    assert record.folders[1].message == "run cancelled (user)"
    assert engine.state is RunState.IDLE
    log = history.log_path(record.id).read_text()
    assert "[WARN] Folder docs: rsync exit 20" in log, (
        "a cancelled folder is a warning, not an error"
    )


async def test_second_start_is_refused_while_running(
    engine: SyncEngine, fake_bin: FakeBinaries
) -> None:
    fake_bin.configure(rsync_mode="hang")
    first = await engine.start(RunTrigger.MANUAL, RunOptions())
    with pytest.raises(RunAlreadyActiveError) as exc:
        await engine.start(RunTrigger.SCHEDULED, RunOptions())
    assert exc.value.run_id == first.id
    await engine.cancel("test")
    await engine.wait()


async def test_unknown_folder_names_are_rejected_before_anything_starts(engine: SyncEngine) -> None:
    with pytest.raises(UnknownFolderError, match="unknown or disabled folder\\(s\\): nope"):
        await engine.start(RunTrigger.MANUAL, RunOptions(folders=["docs", "nope"]))
    assert engine.state is RunState.IDLE


async def test_folder_subset_runs_in_the_requested_order(
    engine: SyncEngine, fake_bin: FakeBinaries, tmp_path: Path
) -> None:
    args_file = tmp_path / "rsync-args"
    fake_bin.configure(rsync_args_file=str(args_file))
    await engine.start(RunTrigger.MANUAL, RunOptions(folders=["photos"]))
    record = await engine.wait()
    assert record is not None and [f.name for f in record.folders] == ["photos"]
    assert len(args_file.read_text().splitlines()) == 1


async def test_ssh_problem_fails_the_run_before_any_folder(
    engine: SyncEngine, history: RunHistory, fake_bin: FakeBinaries
) -> None:
    fake_bin.configure(ssh_keygen_mode="passphrase")
    await engine.start(RunTrigger.MANUAL, RunOptions())
    record = await engine.wait()

    assert record is not None
    assert record.outcome is RunOutcome.FAILED
    assert record.reason is not None and "passphrase-protected" in record.reason
    assert all(f.status is FolderStatus.SKIPPED for f in record.folders)
    assert "[ERROR] SSH key" in history.log_path(record.id).read_text()


async def test_lock_held_by_another_process_fails_the_run(
    engine: SyncEngine, paths: ConfigPaths
) -> None:
    other = (paths.runs_dir / ".lock").open("a")
    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        await engine.start(RunTrigger.MANUAL, RunOptions())
        record = await engine.wait()
    finally:
        other.close()

    assert record is not None
    assert record.outcome is RunOutcome.FAILED
    assert record.reason == "another process holds the run lock; is run-once active?"


async def test_wait_and_cancel_when_idle(engine: SyncEngine) -> None:
    assert await engine.wait() is None
    assert await engine.cancel() is None
    assert engine.last is None

    await engine.start(RunTrigger.MANUAL, RunOptions(folders=["docs"]))
    finished = await engine.wait()
    assert finished is not None
    assert await engine.wait() is finished, "after the run, wait() reports the last result"
    assert engine.last is finished


async def _wait_for(condition: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)


def state_of(engine: SyncEngine) -> RunState:
    return engine.state
