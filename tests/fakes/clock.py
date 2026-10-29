"""A clock that only moves when a test says so."""

import asyncio
from datetime import UTC, datetime, timedelta


class FakeClock:
    """`sleep` advances time instantly, but never past `limit`; at the limit it just yields.

    That lets a scheduler loop run deterministically up to a chosen instant.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
        self.limit: datetime | None = None
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        target = self.current + timedelta(seconds=seconds)
        if self.limit is not None and target > self.limit:
            self.current = max(self.current, self.limit)
            await asyncio.sleep(0.005)
            return
        self.current = target
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
