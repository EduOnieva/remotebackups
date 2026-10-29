"""What a run is made of. These models are what gets persisted and served."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class RunTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class RunState(StrEnum):
    """What the engine is doing right now."""

    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"


class RunOutcome(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    CANCELLED = "cancelled"
    FAILED = "failed"
    # The application stopped (crash, SIGKILL) while the run was in progress
    ABORTED = "aborted"


class FolderStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    # Interrupted by a Stop; not an error, rsync resumes it on the next run
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class RunOptions(BaseModel):
    """What a caller chooses when starting a run."""

    delete: bool = False
    # rsync --timeout; None means none (manual runs), scheduled runs pass the configured value
    rsync_timeout: int | None = None
    # Restrict the run to these folder names; None means every enabled folder
    folders: list[str] | None = None


class RsyncStats(BaseModel):
    """The numbers from rsync's `--stats` block that matter for a backup."""

    files_total: int = 0
    files_transferred: int = 0
    files_created: int = 0
    files_deleted: int = 0
    total_size: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    speedup: float | None = None


class FolderResult(BaseModel):
    name: str
    source: str
    destination: str
    status: FolderStatus = FolderStatus.PENDING
    exit_code: int | None = None
    # Human-readable reason for a failure or skip
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: RsyncStats | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_s(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class RunRecord(BaseModel):
    id: str
    trigger: RunTrigger
    delete: bool
    started_at: datetime
    finished_at: datetime | None = None
    outcome: RunOutcome = RunOutcome.RUNNING
    folders: list[FolderResult] = Field(default_factory=list)
    # Why the run did not finish on its own: "user", "shutdown", a lock message, ...
    reason: str | None = None
    # rsync lines dropped once the per-run log cap was reached
    log_lines_dropped: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def folders_ok(self) -> int:
        return sum(1 for f in self.folders if f.status is FolderStatus.OK)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def folders_failed(self) -> int:
        return sum(1 for f in self.folders if f.status is FolderStatus.FAILED)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def files_transferred(self) -> int:
        return sum(f.stats.files_transferred for f in self.folders if f.stats)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bytes_sent(self) -> int:
        return sum(f.stats.bytes_sent for f in self.folders if f.stats)

    @property
    def current_folder(self) -> FolderResult | None:
        return next((f for f in self.folders if f.status is FolderStatus.RUNNING), None)
