"""Run one child process, stream its output and be able to stop it."""

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Long enough for rsync to flush the current block, short enough for the UI to feel responsive
DEFAULT_GRACE_SECONDS = 10.0
STREAM_LIMIT = 1024 * 1024


class ProcessRunner:
    """Spawns a process in its own session so one signal reaches rsync, ssh and sshpass."""

    def __init__(
        self, on_line: Callable[[str], None], grace_seconds: float = DEFAULT_GRACE_SECONDS
    ) -> None:
        self._on_line = on_line
        self._grace = grace_seconds
        self._process: asyncio.subprocess.Process | None = None

    async def run(self, argv: list[str], env: dict[str, str]) -> int:
        """Run to completion and return the exit code (negative when killed by a signal)."""
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
            limit=STREAM_LIMIT,
        )
        assert self._process.stdout is not None
        try:
            while True:
                raw = await self._process.stdout.readline()
                if not raw:
                    break
                self._on_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
            return await self._process.wait()
        finally:
            self._process = None

    async def cancel(self) -> None:
        """SIGTERM the process group, escalate to SIGKILL after the grace period."""
        process = self._process
        if process is None or process.returncode is not None:
            return
        self._signal_group(process, signal.SIGTERM)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), self._grace)
            return
        logger.warning("Process %d ignored SIGTERM for %.0fs; killing it", process.pid, self._grace)
        self._signal_group(process, signal.SIGKILL)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), self._grace)

    @staticmethod
    def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        # start_new_session makes the pid the pgid; the group may already be gone
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, sig)
