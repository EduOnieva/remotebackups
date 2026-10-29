"""What the web layer needs from the rest of the application, as narrow protocols.

Tests hand in fakes; `Application` hands in the real engine, history and services.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from remotebackups.config import Config
from remotebackups.sync import RunHistory, RunOptions, RunRecord, RunState, RunTrigger


class EngineLike(Protocol):
    @property
    def state(self) -> RunState: ...

    @property
    def current(self) -> RunRecord | None: ...

    async def start(self, trigger: RunTrigger, options: RunOptions) -> RunRecord: ...

    async def cancel(self, reason: str = "user") -> RunRecord | None: ...


class TrueNASLike(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def pool_status(self) -> dict[str, int | str]: ...


class RemoteStatusLike(Protocol):
    @property
    def ok(self) -> bool | None: ...

    @property
    def message(self) -> str: ...

    @property
    def checked_at(self) -> datetime | None: ...


@dataclass
class Services:
    engine: EngineLike
    history: RunHistory
    truenas: TrueNASLike
    config_provider: Callable[[], Config]
    next_run_at: Callable[[], datetime | None]
    scheduler_alive: Callable[[], bool]
    remote_status: Callable[[], RemoteStatusLike]
    version: str
    started_at: datetime
