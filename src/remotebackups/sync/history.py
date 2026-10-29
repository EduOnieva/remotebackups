"""Run records on disk: one `<id>.json` and one `<id>.log` per run under /config/runs."""

import logging
import secrets
from datetime import timedelta
from pathlib import Path

from remotebackups.clock import Clock
from remotebackups.sync.models import FolderStatus, RunOutcome, RunRecord

logger = logging.getLogger(__name__)

ABORTED_LINE = "[ERROR] Run aborted: the application stopped while this run was in progress"


class RunHistory:
    def __init__(self, runs_dir: Path, keep_days: int, clock: Clock) -> None:
        self._dir = runs_dir
        self._keep = timedelta(days=keep_days)
        self._clock = clock

    def new_run_id(self) -> str:
        """Sortable by time, unique within a second."""
        return f"{self._clock.now():%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"

    def record_path(self, run_id: str) -> Path:
        return self._dir / f"{run_id}.json"

    def log_path(self, run_id: str) -> Path:
        return self._dir / f"{run_id}.log"

    def save(self, record: RunRecord) -> None:
        target = self.record_path(record.id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(target)

    def get(self, run_id: str) -> RunRecord | None:
        path = self.record_path(run_id)
        if not path.is_file():
            return None
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self, limit: int | None = None) -> list[RunRecord]:
        """Newest first."""
        paths = sorted(self._dir.glob("*.json"), reverse=True)
        if limit is not None:
            paths = paths[:limit]
        records = []
        for path in paths:
            try:
                records.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                logger.warning("Ignoring unreadable run record %s", path)
        return records

    def last(self) -> RunRecord | None:
        records = self.list_runs(limit=1)
        return records[0] if records else None

    def prune(self) -> int:
        """Delete runs older than the retention window. Returns how many were removed."""
        cutoff = self._clock.now() - self._keep
        removed = 0
        for record in self.list_runs():
            if record.started_at < cutoff:
                self.record_path(record.id).unlink(missing_ok=True)
                self.log_path(record.id).unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.info("Pruned %d run(s) older than %d days", removed, self._keep.days)
        return removed

    def clear(self) -> int:
        removed = 0
        for path in list(self._dir.glob("*.json")) + list(self._dir.glob("*.log")):
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def reconcile_unfinished(self) -> list[RunRecord]:
        """Runs still marked running were cut short by a crash; close them as aborted."""
        now = self._clock.now()
        aborted = []
        for record in self.list_runs():
            if record.outcome is not RunOutcome.RUNNING:
                continue
            record.outcome = RunOutcome.ABORTED
            record.finished_at = now
            record.reason = "application stopped mid-run"
            for folder in record.folders:
                if folder.status in (FolderStatus.RUNNING, FolderStatus.PENDING):
                    folder.status = FolderStatus.SKIPPED
                    folder.message = "application stopped before this folder finished"
                    folder.finished_at = folder.finished_at or now
            self.save(record)
            with self.log_path(record.id).open("a", encoding="utf-8") as log:
                log.write(f"{now.astimezone():%Y-%m-%d %H:%M:%S} {ABORTED_LINE}\n")
            logger.error("Run %s was aborted by an application stop", record.id)
            aborted.append(record)
        return aborted
