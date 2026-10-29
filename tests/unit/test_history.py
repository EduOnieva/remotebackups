from datetime import timedelta
from pathlib import Path

from remotebackups.sync.history import ABORTED_LINE, RunHistory
from remotebackups.sync.models import (
    FolderResult,
    FolderStatus,
    RunOutcome,
    RunRecord,
    RunTrigger,
)
from tests.fakes.clock import FakeClock


def record(history: RunHistory, clock: FakeClock, **overrides: object) -> RunRecord:
    values: dict[str, object] = {
        "id": history.new_run_id(),
        "trigger": RunTrigger.MANUAL,
        "delete": False,
        "started_at": clock.now(),
        "outcome": RunOutcome.COMPLETED,
    }
    values.update(overrides)
    rec = RunRecord.model_validate(values)
    history.save(rec)
    history.log_path(rec.id).write_text("log\n")
    return rec


def test_ids_sort_chronologically(tmp_path: Path, clock: FakeClock) -> None:
    history = RunHistory(tmp_path, keep_days=40, clock=clock)
    first = history.new_run_id()
    clock.advance(1)
    second = history.new_run_id()
    assert first < second
    assert first.startswith("20261029T120000Z-")


def test_save_get_list_last(tmp_path: Path, clock: FakeClock) -> None:
    history = RunHistory(tmp_path, keep_days=40, clock=clock)
    older = record(history, clock)
    clock.advance(60)
    newer = record(history, clock)

    assert history.get(older.id) == older
    assert history.get("missing") is None
    assert [r.id for r in history.list_runs()] == [newer.id, older.id]
    assert [r.id for r in history.list_runs(limit=1)] == [newer.id]
    assert history.last() == newer
    assert not (tmp_path / f"{newer.id}.json.tmp").exists()


def test_unreadable_record_is_skipped(tmp_path: Path, clock: FakeClock) -> None:
    history = RunHistory(tmp_path, keep_days=40, clock=clock)
    kept = record(history, clock)
    (tmp_path / "99999999T000000Z-zzzz.json").write_text("{not json")
    assert [r.id for r in history.list_runs()] == [kept.id]


def test_prune_removes_runs_older_than_keep_days_with_their_logs(
    tmp_path: Path, clock: FakeClock
) -> None:
    history = RunHistory(tmp_path, keep_days=40, clock=clock)
    old = record(history, clock, started_at=clock.now() - timedelta(days=41))
    fresh = record(history, clock, started_at=clock.now() - timedelta(days=39))

    assert history.prune() == 1
    assert history.get(old.id) is None
    assert not history.log_path(old.id).exists()
    assert history.get(fresh.id) is not None
    assert history.log_path(fresh.id).exists()


def test_clear_removes_everything(tmp_path: Path, clock: FakeClock) -> None:
    history = RunHistory(tmp_path, keep_days=40, clock=clock)
    record(history, clock)
    record(history, clock)
    assert history.clear() == 4
    assert history.list_runs() == []


def test_reconcile_marks_running_runs_as_aborted(tmp_path: Path, clock: FakeClock) -> None:
    history = RunHistory(tmp_path, keep_days=40, clock=clock)
    finished = record(history, clock)
    crashed = record(
        history,
        clock,
        outcome=RunOutcome.RUNNING,
        folders=[
            FolderResult(name="a", source="/a", destination="/b", status=FolderStatus.OK),
            FolderResult(name="b", source="/b", destination="/c", status=FolderStatus.RUNNING),
            FolderResult(name="c", source="/c", destination="/d"),
        ],
    )
    clock.advance(30)

    aborted = history.reconcile_unfinished()

    assert [r.id for r in aborted] == [crashed.id]
    reloaded = history.get(crashed.id)
    assert reloaded is not None
    assert reloaded.outcome is RunOutcome.ABORTED
    assert reloaded.finished_at == clock.now()
    assert [f.status for f in reloaded.folders] == [
        FolderStatus.OK,
        FolderStatus.SKIPPED,
        FolderStatus.SKIPPED,
    ]
    assert history.log_path(crashed.id).read_text().rstrip().endswith(ABORTED_LINE)
    assert history.get(finished.id) == finished
