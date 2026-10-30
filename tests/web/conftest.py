from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from remotebackups.config import Config
from remotebackups.config.paused import PausedFolders
from remotebackups.paths import ConfigPaths
from remotebackups.sync import (
    FolderResult,
    FolderStatus,
    RunAlreadyActiveError,
    RunHistory,
    RunOptions,
    RunOutcome,
    RunRecord,
    RunState,
    RunTrigger,
    UnknownFolderError,
)
from remotebackups.truenas.service import TrueNASDetails
from remotebackups.web import Services, create_app
from tests.fakes.clock import FakeClock


@dataclass
class FakeRemote:
    ok: bool | None = True
    message: str = "ok"
    checked_at: datetime | None = None
    probes: int = 0
    # What the next probe will report, so a test can watch "no" turn into "yes"
    next_ok: bool = True
    next_message: str = "ok"

    async def probe(self) -> "FakeRemote":
        self.probes += 1
        self.ok, self.message = self.next_ok, self.next_message
        return self


class FakeEngine:
    """Records starts and cancels; `current` is whatever the test sets."""

    def __init__(self, history: RunHistory, config: Config, clock: FakeClock) -> None:
        self._history = history
        self._config = config
        self._clock = clock
        self.current: RunRecord | None = None
        self.cancelled: list[str] = []
        self.starts: list[RunOptions] = []

    @property
    def state(self) -> RunState:
        return RunState.IDLE if self.current is None else RunState.RUNNING

    async def start(self, trigger: RunTrigger, options: RunOptions) -> RunRecord:
        if self.current is not None:
            raise RunAlreadyActiveError(self.current.id)
        names = {f.name for f in self._config.enabled_folders}
        unknown = [n for n in options.folders or [] if n not in names]
        if unknown:
            raise UnknownFolderError(unknown)
        self.starts.append(options)
        record = RunRecord(
            id=self._history.new_run_id(),
            trigger=trigger,
            delete=options.delete,
            dry_run=options.dry_run,
            started_at=self._clock.now(),
            folders=[
                FolderResult(
                    name=f.name,
                    source=f.source,
                    destination=f.destination,
                    status=FolderStatus.RUNNING,
                )
                for f in self._config.enabled_folders
            ],
        )
        self._history.save(record)
        self._history.log_path(record.id).write_text("started\n")
        self.current = record
        return record

    async def cancel(self, reason: str = "user") -> RunRecord | None:
        if self.current is None:
            return None
        self.cancelled.append(reason)
        return self.current


class FakeTrueNAS:
    def __init__(self) -> None:
        self.enabled = False
        self.answer: dict[str, int | str] = {"truenas": "disabled"}
        self.detail = TrueNASDetails(enabled=False)

    async def pool_status(self) -> dict[str, int | str]:
        return dict(self.answer)

    async def details(self) -> TrueNASDetails:
        return self.detail


@dataclass
class Harness:
    paths: ConfigPaths
    config: Config
    history: RunHistory
    engine: FakeEngine
    truenas: FakeTrueNAS
    remote: FakeRemote
    clock: FakeClock
    next_run_at: datetime | None = None
    scheduler_alive: bool = True
    finished: list[RunRecord] = field(default_factory=list)

    def finished_run(
        self, outcome: RunOutcome = RunOutcome.COMPLETED, failed: int = 0
    ) -> RunRecord:
        """A completed run in history, newest by construction."""
        self.clock.advance(60)
        folders = [
            FolderResult(
                name=f.name,
                source=f.source,
                destination=f.destination,
                status=FolderStatus.FAILED if i < failed else FolderStatus.OK,
                exit_code=23 if i < failed else 0,
            )
            for i, f in enumerate(self.config.enabled_folders)
        ]
        record = RunRecord(
            id=self.history.new_run_id(),
            trigger=RunTrigger.SCHEDULED,
            delete=False,
            started_at=self.clock.now(),
            finished_at=self.clock.now(),
            outcome=outcome,
            folders=folders,
        )
        self.history.save(record)
        self.history.log_path(record.id).write_text("line one\nline two\n")
        self.finished.append(record)
        return record


@pytest.fixture
def harness(paths: ConfigPaths, clock: FakeClock, make_config: Callable[..., Config]) -> Harness:
    config = make_config()
    history = RunHistory(paths.runs_dir, keep_days=40, clock=clock)
    return Harness(
        paths=paths,
        config=config,
        history=history,
        engine=FakeEngine(history, config, clock),
        truenas=FakeTrueNAS(),
        remote=FakeRemote(checked_at=clock.now()),
        clock=clock,
    )


@pytest.fixture
async def client(harness: Harness) -> AsyncIterator[AsyncClient]:
    services = Services(
        engine=harness.engine,
        history=harness.history,
        truenas=harness.truenas,
        config_provider=lambda: harness.config,
        next_run_at=lambda: harness.next_run_at,
        scheduler_alive=lambda: harness.scheduler_alive,
        remote_status=lambda: harness.remote,
        probe_remote=harness.remote.probe,
        paused=PausedFolders(harness.paths.paused_file),
        version="2.0.0-test",
        started_at=harness.clock.now(),
    )
    app = create_app(services, sse_poll_seconds=0.02)
    # The page sends Content-Type: application/json on every call; the client does the same so
    # the CSRF guard on the mutating routes lets the tests through
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Content-Type": "application/json"},
    ) as http:
        yield http
