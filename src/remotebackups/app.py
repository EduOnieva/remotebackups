"""The long-running service: wires the parts together and shuts them down in order."""

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from remotebackups.clock import Clock, SystemClock
from remotebackups.config import Config, ConfigError, EnvSettings, load_config
from remotebackups.config.loader import ensure_config_files
from remotebackups.paths import ConfigPaths
from remotebackups.scheduler import CronScheduler
from remotebackups.sync import RunHistory, SshSetup, SshSetupError, SyncEngine

logger = logging.getLogger(__name__)

PROBE_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class RemoteStatus:
    """Result of the last SSH probe; `ok` is None until the first one completes."""

    ok: bool | None
    message: str
    checked_at: datetime | None


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
        # Built by run(); typed as Optional so a web layer can ask before startup finished
        self.history: RunHistory | None = None
        self.engine: SyncEngine | None = None
        self.scheduler: CronScheduler | None = None

    def load_config(self) -> Config:
        return load_config(self.paths, self.env)

    async def run(self) -> int:
        """Serve until a shutdown is requested. Raises ConfigError when the config is unusable."""
        self.started_at = self.clock.now()
        self.paths.ensure()
        ensure_config_files(self.paths)
        config = self.load_config()
        if not self.paths.config_dir_is_mounted():
            logger.warning(
                "%s is not a mounted volume; configuration and history will be lost with the "
                "container",
                self.paths.config_dir,
            )
        logger.info(
            "remotebackups starting: folders=%d schedule=%s remote=%s@%s",
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
        self.scheduler.start()
        self._probe_task = asyncio.create_task(self._probe_loop(), name="remote-probe")

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
        try:
            config = self.load_config()
            ssh = SshSetup(self.paths, config.remote)
            result = await ssh.probe(ssh.prepare())
            ok, message = result.ok, result.message
        except (ConfigError, SshSetupError) as exc:
            ok, message = False, str(exc)
        previous = self.remote_status
        self.remote_status = RemoteStatus(ok, message, self.clock.now())
        if ok and previous.ok is False:
            logger.info("Remote host reachable again")
        elif not ok and previous.message != message:
            # A sleeping NAS or a VPN that is still coming up is expected; the UI shows it
            logger.warning("Remote host check failed: %s", message)
        return self.remote_status

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
        logger.info("remotebackups stopped (%s)", self._shutdown_reason)
