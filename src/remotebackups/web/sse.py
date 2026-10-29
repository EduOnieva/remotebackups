"""Live tail of a run log as Server-Sent Events.

The stream reads the log file itself, not memory, so it works after a restart and resumes
from `Last-Event-ID`, which carries the byte offset already delivered.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from remotebackups.sync import RunHistory, RunOutcome

HEARTBEAT_SECONDS = 15.0


def _read_complete_lines(path: Path, offset: int) -> tuple[list[tuple[str, int]], int]:
    """Lines after `offset` that already end in a newline, each with the offset after it."""
    if not path.exists():
        return [], offset
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    lines: list[tuple[str, int]] = []
    position = offset
    for raw in chunk[:end].split(b"\n"):
        position += len(raw) + 1
        lines.append((raw.decode("utf-8", "replace"), position))
    return lines, position


def _event(name: str, data: str, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    body = "\n".join(f"data: {part}" for part in data.split("\n"))
    return f"{head}event: {name}\n{body}\n\n"


async def stream_run_log(
    history: RunHistory, run_id: str, start_offset: int, poll_seconds: float = 0.5
) -> AsyncIterator[str]:
    """Yield `line` events with the byte offset as id, `state` on changes, then `end`."""
    path = history.log_path(run_id)
    offset = start_offset
    last_outcome: RunOutcome | None = None
    idle = 0.0
    while True:
        lines, offset = _read_complete_lines(path, offset)
        for text, line_offset in lines:
            yield _event("line", text, line_offset)
        record = history.get(run_id)
        if record is not None and record.outcome is not last_outcome:
            last_outcome = record.outcome
            yield _event("state", record.outcome.value)
        finished = record is None or record.outcome is not RunOutcome.RUNNING
        if finished and not lines:
            yield _event("end", record.outcome.value if record else "missing")
            return
        if lines:
            idle = 0.0
            continue
        await asyncio.sleep(poll_seconds)
        idle += poll_seconds
        if idle >= HEARTBEAT_SECONDS:
            idle = 0.0
            yield ": keep-alive\n\n"
