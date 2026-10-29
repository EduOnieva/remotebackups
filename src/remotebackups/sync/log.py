"""The per-run log file: rsync output plus the engine's own tagged lines."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

CAP_MESSAGE = "Log cap reached; further rsync output is counted but not stored"


class RunLog:
    """Append-only writer with a byte cap and a change signal for live viewers."""

    def __init__(self, path: Path, max_bytes: int, now: Callable[[], datetime]) -> None:
        self.path = path
        self._max_bytes = max_bytes
        self._now = now
        self._size = 0
        self._capped = False
        self.lines_dropped = 0
        self._changed = asyncio.Event()
        self._file = path.open("a", encoding="utf-8", buffering=1)

    def event(self, level: str, message: str) -> None:
        """An engine line: `2026-10-29 14:00:00 [INFO] message`. Never dropped by the cap."""
        stamp = self._now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self._write(f"{stamp} [{level}] {message}\n", bypass_cap=True)

    def output(self, line: str) -> None:
        """A raw rsync line. Subject to the cap."""
        self._write(line.rstrip("\n") + "\n", bypass_cap=False)

    def close(self) -> None:
        self._file.close()

    async def wait_for_change(self, max_wait: float) -> bool:
        """Block until something was written or `max_wait` seconds passed; True on new data."""
        try:
            await asyncio.wait_for(self._changed.wait(), max_wait)
        except TimeoutError:
            return False
        return True

    def _write(self, text: str, *, bypass_cap: bool) -> None:
        if not bypass_cap and self._size + len(text) > self._max_bytes:
            self.lines_dropped += 1
            if not self._capped:
                self._capped = True
                self.event("WARN", CAP_MESSAGE)
            return
        self._file.write(text)
        self._size += len(text.encode("utf-8"))
        # set() wakes every current waiter; clearing right after re-arms it for the next write
        self._changed.set()
        self._changed.clear()
