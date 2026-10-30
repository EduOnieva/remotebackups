"""The long-running service: wires the parts together and shuts them down in order."""

import asyncio
import contextlib
import logging
import signal
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn

from remotebackups import __version__
from remotebackups.clock import Clock, SystemClock
from remotebackups.config import Config, ConfigError, EnvSettings, load_config
from remotebackups.config.loader import ensure_config_files
from remotebackups.config.paused import PausedFolders
from remotebackups.paths import ConfigPaths
from remotebackups.scheduler import CronScheduler
from remotebackups.sync import RunHistory, SshSetup, SshSetupError, SyncEngine
from remotebackups.truenas import TrueNASService
from remotebackups.web import Services, create_app

logger = logging.getLogger(__name__)

PROBE_INTERVAL_SECONDS = 300.0
# Open SSE streams would otherwise hold the server up for uvicorn's default graceful period
WEB_SHUTDOWN_TIMEOUT_SECONDS = 3


class WebStartError(Exception):
    """The web UI could not start listening; the message says why."""


@dataclass(frozen=True)
class RemoteStatus:
    """Result of the last SSH probe; `ok` is None until the first one completes."""

    ok: bool | None
    message: str
    checked_at: datetime | None


class _QuietServer(uvicorn.Server):
    """uvicorn without its own signal handling: the Application owns SIGTERM and SIGINT."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class Application:
    def __init__(self, env: EnvSettings, paths: ConfigPaths, clock: Clock | None = None) -> None:
        self.env = env
        self.paths = paths
        self.clock: Clock = clock or SystemClock()
        self.tz = ZoneInfo(env.tz)
        self.started_at: datetime | None = None
        self.remote_status = RemoteStatus(None, "not checked yet", None)
        self._shutdown = asyncio.Event()
        self._shutdown_reason = ""
        self._probe_task: asyncio.Task[None] | None = None
        # The periodic loop and "Check now" from the UI must not run two ssh probes at once
        self._probe_lock = asyncio.Lock()
        self._server: _QuietServer | None = None
        self._serve_task: asyncio.Task[None] | None = None
        # Built by run(); typed as Optional so callers can ask before startup finished
        self.history: RunHistory | None = None
        self.engine: SyncEngine | None = None
        self.scheduler: CronScheduler | None = None
        self.truenas: TrueNASService | None = None

    def load_config(self) -> Config:
        return load_config(self.paths, self.env)

    @property
    def web_port(self) -> int | None:
        """The port actually bound, once the server listens (useful when WEB_PORT=0)."""
        if self._server is None or not self._server.started or not self._server.servers:
            return None
        sockets = self._server.servers[0].sockets
        port: int = sockets[0].getsockname()[1]
        return port

    async def run(self) -> int:
        """Serve until a shutdown is requested. Raises ConfigError when the config is unusable."""
        self.started_at = self.clock.now()
        self.paths.ensure()
        ensure_config_files(self.paths)
        forgotten = PausedFolders(self.paths.paused_file).clear()
        if forgotten:
            logger.info(
                "Folders paused before the restart run again: %s", ", ".join(sorted(forgotten))
            )
        config = self.load_config()
        if not self.paths.config_dir_is_mounted():
            logger.warning(
                "%s is not a mounted volume; configuration and history will be lost with the "
                "container",
                self.paths.config_dir,
            )
        logger.info(
            "remotebackups %s starting: folders=%d schedule=%s remote=%s@%s",
            __version__,
            len(config.enabled_folders),
            config.schedule.cron or "disabled",
            config.remote.user,
            config.remote.host,
        )

        self.history = RunHistory(self.paths.runs_dir, config.history.keep_days, self.clock)
        self.history.reconcile_unfinished()
        self.history.prune()
        self.engine = SyncEngine(self.load_config, self.paths, self.history, self.clock)
        self.scheduler = CronScheduler(self.load_config, self.tz, self.engine, self.clock)
        self.truenas = TrueNASService(self.load_config, self.clock)
        self.scheduler.start()
        self._probe_task = asyncio.create_task(self._probe_loop(), name="remote-probe")
        try:
            await self._start_web()
        except WebStartError:
            await self._shutdown_sequence()
            raise

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_shutdown, sig.name)
        try:
            await self._shutdown.wait()
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
            await self._shutdown_sequence()
        return 0

    def request_shutdown(self, reason: str) -> None:
        if not self._shutdown.is_set():
            self._shutdown_reason = reason
            logger.info("Shutdown requested: %s", reason)
            self._shutdown.set()

    async def probe_remote(self) -> RemoteStatus:
        """Check the SSH login once and remember the result for the UI."""
        async with self._probe_lock:
            try:
                config = self.load_config()
                ssh = SshSetup(self.paths, config.remote)
                result = await ssh.probe(ssh.prepare())
                ok, message = result.ok, result.message
            except (ConfigError, SshSetupError) as exc:
                ok, message = False, str(exc)
            except Exception as exc:
                # A bug here would otherwise end the periodic loop and leave a stale status
                logger.exception("Remote probe crashed")
                ok, message = False, f"internal error: {exc}"
            return self._record_probe(ok, message)

    def _record_probe(self, ok: bool, message: str) -> RemoteStatus:
        previous = self.remote_status
        self.remote_status = RemoteStatus(ok, message, self.clock.now())
        if ok and previous.ok is False:
            logger.info("Remote host reachable again")
        elif not ok and previous.message != message:
            # A sleeping NAS or a VPN that is still coming up is expected; the UI shows it
            logger.warning("Remote host check failed: %s", message)
        return self.remote_status

    def services(self) -> Services:
        assert self.engine and self.history and self.scheduler and self.truenas
        assert self.started_at is not None
        scheduler = self.scheduler
        return Services(
            engine=self.engine,
            history=self.history,
            truenas=self.truenas,
            config_provider=self.load_config,
            next_run_at=lambda: scheduler.next_run_at,
            scheduler_alive=lambda: scheduler.alive,
            remote_status=lambda: self.remote_status,
            probe_remote=self.probe_remote,
            paused=PausedFolders(self.paths.paused_file),
            version=__version__,
            started_at=self.started_at,
        )

    async def _start_web(self) -> None:
        # Binding here rather than in uvicorn: uvicorn answers a busy port with sys.exit()
        # from inside its task, which surfaces as tracebacks instead of a sentence
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self.env.web_port))
        except OSError as exc:
            sock.close()
            raise WebStartError(
                f"the web UI cannot listen on port {self.env.web_port} ({exc.strerror}); "
                "is another service using it? WEB_PORT selects a different one"
            ) from exc
        config = uvicorn.Config(
            create_app(self.services()),
            log_config=None,
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=WEB_SHUTDOWN_TIMEOUT_SECONDS,
        )
        self._server = _QuietServer(config)
        self._serve_task = asyncio.create_task(self._server.serve(sockets=[sock]), name="web")
        while not self._server.started:
            if self._serve_task.done():
                self._serve_task.result()
                raise WebStartError(
                    f"the web server exited while starting on port {self.env.web_port}"
                )
            await asyncio.sleep(0.01)
        logger.info("Web UI listening on port %d", self.web_port or self.env.web_port)

    async def _probe_loop(self) -> None:
        while True:
            await self.probe_remote()
            await self.clock.sleep(PROBE_INTERVAL_SECONDS)

    async def _shutdown_sequence(self) -> None:
        # Order matters: nothing new may start, then the active run is stopped, then we exit
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self._probe_task is not None:
            self._probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._probe_task
        if self.engine is not None and self.engine.current is not None:
            await self.engine.cancel("shutdown")
        if self._server is not None and self._serve_task is not None:
            self._server.should_exit = True
            await self._serve_task
        logger.info("remotebackups stopped (%s)", self._shutdown_reason)
