"""Response and request bodies of the JSON API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from remotebackups.sync import FolderResult, RunOutcome, RunRecord, RunState, RunTrigger


class RunSummary(BaseModel):
    id: str
    trigger: RunTrigger
    delete: bool
    dry_run: bool
    outcome: RunOutcome
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    folders_total: int
    # Names in run order, so a one-folder run started from its row says which one
    folder_names: list[str]
    folders_ok: int
    folders_failed: int
    files_transferred: int
    bytes_sent: int
    reason: str | None
    current_folder: str | None

    @classmethod
    def from_record(cls, record: RunRecord) -> "RunSummary":
        current = record.current_folder
        return cls(
            id=record.id,
            trigger=record.trigger,
            delete=record.delete,
            dry_run=record.dry_run,
            outcome=record.outcome,
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_s=record.duration_s,
            folders_total=len(record.folders),
            folder_names=[f.name for f in record.folders],
            folders_ok=record.folders_ok,
            folders_failed=record.folders_failed,
            files_transferred=record.files_transferred,
            bytes_sent=record.bytes_sent,
            reason=record.reason,
            current_folder=current.name if current else None,
        )


class RunDetail(RunSummary):
    folders: list[FolderResult]
    log_lines_dropped: int

    @classmethod
    def from_record(cls, record: RunRecord) -> "RunDetail":
        summary = RunSummary.from_record(record)
        return cls(
            **summary.model_dump(),
            folders=record.folders,
            log_lines_dropped=record.log_lines_dropped,
        )


class RemoteInfo(BaseModel):
    host: str | None
    user: str | None
    port: int
    auth: Literal["key", "password"]
    host_key_policy: str
    ok: bool | None
    message: str
    checked_at: datetime | None


class StatusResponse(BaseModel):
    state: RunState
    current_run: RunSummary | None
    last_run: RunSummary | None
    next_run_at: datetime | None
    schedule: str | None
    remote: RemoteInfo
    truenas_enabled: bool
    dark_theme: bool
    locale: str | None
    clock: str
    version: str
    started_at: datetime


class FolderInfo(BaseModel):
    name: str
    source: str
    destination: str
    enabled: bool
    # Paused from the UI until the container restarts; independent of `enabled`
    paused: bool
    allow_delete: bool
    source_exists: bool


class FolderUpdate(BaseModel):
    paused: bool


class RunRequest(BaseModel):
    delete: bool = False
    dry_run: bool = False
    folders: list[str] | None = Field(default=None, min_length=1)


class RunAccepted(BaseModel):
    run_id: str
    state: RunState


class HealthResponse(BaseModel):
    status: str
    version: str
    reasons: list[str] = Field(default_factory=list)
    next_run_at: datetime | None = None
