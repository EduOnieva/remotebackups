"""The run lifecycle: one run at a time, folder by folder, stoppable at any point."""

import asyncio
import fcntl
import logging
from collections.abc import Callable
from pathlib import Path
from typing import IO

from remotebackups.clock import Clock
from remotebackups.config.schema import Config, FolderConfig
from remotebackups.paths import ConfigPaths
from remotebackups.sync.command import RsyncCommand, RsyncCommandBuilder
from remotebackups.sync.executor import ProcessRunner
from remotebackups.sync.exit_codes import PARTIAL_TRANSFER_CODES, describe_exit
from remotebackups.sync.history import RunHistory
from remotebackups.sync.log import RunLog
from remotebackups.sync.models import (
    FolderResult,
    FolderStatus,
    RunOptions,
    RunOutcome,
    RunRecord,
    RunState,
    RunTrigger,
)
from remotebackups.sync.ssh import SshSetup, SshSetupError, minimal_env
from remotebackups.sync.stats import parse_stats

logger = logging.getLogger(__name__)

# Lines rsync prints before each --stats block; everything after belongs to the stats
STATS_HEADER = "Number of files:"


class RunAlreadyActiveError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} is already active")
        self.run_id = run_id


class UnknownFolderError(ValueError):
    def __init__(self, names: list[str]) -> None:
        super().__init__("unknown or disabled folder(s): " + ", ".join(names))
        self.names = names


class SyncEngine:
    """Starts runs in the background and reports on the active one.

    `config_provider` is called at the start of every run so folder edits need no restart.
    """

    def __init__(
        self,
        config_provider: Callable[[], Config],
        paths: ConfigPaths,
        history: RunHistory,
        clock: Clock,
        grace_seconds: float = 10.0,
    ) -> None:
        self._config_provider = config_provider
        self._paths = paths
        self._history = history
        self._clock = clock
        self._grace = grace_seconds
        self._active: asyncio.Task[RunRecord] | None = None
        self._current: RunRecord | None = None
        self._last: RunRecord | None = None
        self._runner: ProcessRunner | None = None
        self._cancel_reason: str | None = None

    @property
    def state(self) -> RunState:
        if self._current is None:
            return RunState.IDLE
        return RunState.CANCELLING if self._cancel_reason else RunState.RUNNING

    @property
    def current(self) -> RunRecord | None:
        return self._current

    async def start(self, trigger: RunTrigger, options: RunOptions) -> RunRecord:
        """Begin a run and return its record immediately; the run proceeds in the background."""
        if self._current is not None:
            raise RunAlreadyActiveError(self._current.id)
        config = self._config_provider()
        folders = self._select_folders(config, options)
        record = RunRecord(
            id=self._history.new_run_id(),
            trigger=trigger,
            delete=options.delete,
            dry_run=options.dry_run,
            started_at=self._clock.now(),
            folders=[
                FolderResult(name=f.name, source=f.source, destination=f.destination)
                for f in folders
            ],
        )
        self._current = record
        self._cancel_reason = None
        self._history.save(record)
        self._active = asyncio.create_task(self._run(record, config, folders, options))
        return record

    async def wait(self) -> RunRecord | None:
        """Block until the active run finishes and return it, else the last finished run."""
        task = self._active
        if task is None:
            return self._last
        return await task

    @property
    def last(self) -> RunRecord | None:
        """The most recently finished run of this process, if any."""
        return self._last

    async def cancel(self, reason: str = "user") -> RunRecord | None:
        """Stop the active run: the current rsync gets SIGTERM, the rest is skipped."""
        if self._current is None:
            return None
        task = self._active
        if self._cancel_reason is None:
            self._cancel_reason = reason
            logger.warning("Run %s cancelled: reason=%s", self._current.id, reason)
        if self._runner is not None:
            await self._runner.cancel()
        # The run task still has to mark the remaining folders and persist the record
        return await task if task is not None else self._current

    async def _run(
        self, record: RunRecord, config: Config, folders: list[FolderConfig], options: RunOptions
    ) -> RunRecord:
        log = RunLog(
            self._history.log_path(record.id), config.history.max_log_bytes, self._clock.now
        )
        lock: IO[str] | None = None
        try:
            paused = ", ".join(sorted(config.paused_folders))
            log.event(
                "INFO",
                f"Run {record.id} started: trigger={record.trigger.value} "
                f"delete={options.delete} dry_run={options.dry_run} folders={len(folders)}"
                + (f" paused={paused}" if paused else ""),
            )
            lock = self._acquire_lock(self._paths.runs_dir / ".lock")
            if lock is None:
                raise SshSetupError("another process holds the run lock; is run-once active?")
            ssh = SshSetup(self._paths, config.remote).prepare()
            builder = RsyncCommandBuilder(config.rsync, config.remote, ssh.command, ssh.password)
            for folder, result in zip(folders, record.folders, strict=True):
                if self._cancel_reason:
                    result.status = FolderStatus.SKIPPED
                    result.message = f"run cancelled ({self._cancel_reason})"
                    continue
                await self._sync_folder(folder, result, builder.build(folder, options), log, record)
                self._history.save(record)
            record.outcome = self._outcome(record)
        except SshSetupError as exc:
            record.outcome = RunOutcome.FAILED
            record.reason = str(exc)
            log.event("ERROR", str(exc))
            for result in record.folders:
                if result.status is FolderStatus.PENDING:
                    result.status = FolderStatus.SKIPPED
                    result.message = "run failed before this folder started"
        except Exception as exc:  # a bug; keep the record coherent and let the log explain
            logger.exception("Run %s crashed", record.id)
            record.outcome = RunOutcome.FAILED
            record.reason = f"internal error: {exc}"
            log.event("ERROR", record.reason)
        finally:
            record.finished_at = self._clock.now()
            record.log_lines_dropped = log.lines_dropped
            log.event(
                "INFO" if record.outcome is RunOutcome.COMPLETED else "WARN",
                f"Run {record.id} finished: outcome={record.outcome.value} "
                f"ok={record.folders_ok} failed={record.folders_failed}",
            )
            log.close()
            self._history.save(record)
            self._history.prune()
            if lock is not None:
                lock.close()
            self._last = record
            self._current = None
            self._runner = None
            self._active = None
            self._cancel_reason = None
        return record

    async def _sync_folder(
        self,
        folder: FolderConfig,
        result: FolderResult,
        command: RsyncCommand,
        log: RunLog,
        record: RunRecord,
    ) -> None:
        result.started_at = self._clock.now()
        result.status = FolderStatus.RUNNING
        self._history.save(record)
        log.event("INFO", f"Folder {folder.name}: {folder.source} -> {folder.destination}")
        if not _source_exists(folder):
            result.status = FolderStatus.FAILED
            result.message = "source directory not found inside the container"
            result.finished_at = self._clock.now()
            log.event("ERROR", f"Folder {folder.name}: {result.message}")
            return
        log.event("INFO", command.display())

        tail: list[str] = []
        in_stats = False

        def on_line(line: str) -> None:
            nonlocal in_stats
            log.output(line)
            if line.startswith(STATS_HEADER):
                in_stats = True
            if in_stats:
                tail.append(line)

        self._runner = ProcessRunner(on_line, self._grace)
        env = {**minimal_env(), **command.env}
        exit_code = await self._runner.run(command.argv, env)
        self._runner = None

        result.exit_code = exit_code
        result.stats = parse_stats(tail)
        result.finished_at = self._clock.now()
        if exit_code == 0:
            result.status = FolderStatus.OK
            log.event("INFO", f"Folder {folder.name}: done")
        elif self._cancel_reason:
            result.status = FolderStatus.CANCELLED
            result.message = f"interrupted by stop ({self._cancel_reason}); rsync exit {exit_code}"
            log.event("WARN", f"Folder {folder.name}: {result.message}")
        else:
            result.status = FolderStatus.FAILED
            meaning = describe_exit(exit_code)
            if exit_code in PARTIAL_TRANSFER_CODES:
                meaning += " (see the rsync messages above)"
            result.message = f"rsync exit {exit_code}: {meaning}"
            log.event("ERROR", f"Folder {folder.name}: {result.message}")

    def _outcome(self, record: RunRecord) -> RunOutcome:
        if self._cancel_reason:
            record.reason = self._cancel_reason
            return RunOutcome.CANCELLED
        if record.folders and record.folders_failed == len(record.folders):
            return RunOutcome.FAILED
        if record.folders_failed:
            return RunOutcome.COMPLETED_WITH_ERRORS
        return RunOutcome.COMPLETED

    @staticmethod
    def _select_folders(config: Config, options: RunOptions) -> list[FolderConfig]:
        enabled = config.enabled_folders
        if options.folders is None:
            return enabled
        by_name = {folder.name: folder for folder in enabled}
        unknown = [name for name in options.folders if name not in by_name]
        if unknown:
            raise UnknownFolderError(unknown)
        return [by_name[name] for name in options.folders]

    @staticmethod
    def _acquire_lock(path: Path) -> IO[str] | None:
        handle = path.open("a")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle


def _source_exists(folder: FolderConfig) -> bool:
    return Path(folder.source).is_dir()
