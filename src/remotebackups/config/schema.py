"""Pydantic model of `/config/config.yaml`.

Every model forbids unknown keys so a typo fails loudly instead of being ignored.
Structural rules live here; anything that needs the filesystem or the network
(key files, reachability) is checked by the module that uses the value.
"""

from collections.abc import Iterable
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from croniter import croniter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)

DEFAULT_RSYNC_OPTIONS = ["-a", "-v", "-z", "--partial", "--stats"]
DEFAULT_EXCLUDES = [
    "*.Trash*",
    "lost+found",
    "System Volume Information",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    ".venv",
]
# Decided per run (manual delete, scheduled timeout, dry run); never part of the options
RUN_LEVEL_OPTIONS = ("--delete", "--timeout", "--dry-run")
MIB = 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HostKeyPolicy(StrEnum):
    TOFU = "tofu"
    STRICT = "strict"
    INSECURE = "insecure"

    @property
    def ssh_value(self) -> str:
        """The matching `StrictHostKeyChecking` value."""
        return {"tofu": "accept-new", "strict": "yes", "insecure": "no"}[self.value]


def _absolute_path(value: str, what: str) -> str:
    """Normalise a path from the config and reject anything relative or escaping."""
    path = PurePosixPath(value)
    if not path.is_absolute():
        raise ValueError(f"{what} must be an absolute path")
    if ".." in path.parts:
        raise ValueError(f"{what} must not contain '..'")
    return str(path)


class RemoteConfig(StrictModel):
    host: str | None = None
    user: str | None = None
    port: int = Field(22, ge=1, le=65535)
    ssh_key: str | None = None
    password: SecretStr | None = None
    host_key_policy: HostKeyPolicy = HostKeyPolicy.TOFU
    host_key: str | None = None

    @model_validator(mode="after")
    def pinned_key_implies_strict(self) -> Self:
        if self.host_key:
            self.host_key_policy = HostKeyPolicy.STRICT
        return self


class ScheduleConfig(StrictModel):
    cron: str | None = "30 2 * * 1"
    rsync_timeout: int = Field(3600, ge=0)

    @field_validator("cron", mode="before")
    @classmethod
    def empty_disables(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("cron")
    @classmethod
    def must_be_valid_cron(cls, value: str | None) -> str | None:
        if value is not None and not croniter.is_valid(value):
            raise ValueError("is not a valid cron expression")
        return value


def _reject_run_level_options(options: list[str]) -> list[str]:
    for option in options:
        if option.startswith(RUN_LEVEL_OPTIONS):
            raise ValueError(f"'{option}' is decided per run and cannot be set here")
    return options


class RsyncConfig(StrictModel):
    options: list[str] = Field(default_factory=lambda: list(DEFAULT_RSYNC_OPTIONS))
    # A non-root remote user cannot chown, so preserving ownership makes every run end in exit 23
    preserve_ownership: bool = False
    extra_options: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDES))

    @field_validator("options", "extra_options")
    @classmethod
    def no_run_level_options(cls, value: list[str]) -> list[str]:
        return _reject_run_level_options(value)


class FolderConfig(StrictModel):
    source: str
    destination: str
    name: str = ""
    excludes: list[str] = Field(default_factory=list)
    extra_options: list[str] = Field(default_factory=list)
    allow_delete: bool = True
    enabled: bool = True

    @field_validator("source")
    @classmethod
    def source_is_absolute(cls, value: str) -> str:
        return _absolute_path(value, "source")

    @field_validator("destination")
    @classmethod
    def destination_is_absolute(cls, value: str) -> str:
        return _absolute_path(value, "destination")

    @field_validator("extra_options")
    @classmethod
    def no_run_level_options(cls, value: list[str]) -> list[str]:
        return _reject_run_level_options(value)

    @model_validator(mode="after")
    def default_name_from_source(self) -> Self:
        if not self.name:
            self.name = PurePosixPath(self.source).name
        if not self.name:
            raise ValueError("name is required when source is '/'")
        if "/" in self.name or self.name.strip() != self.name:
            raise ValueError("name must not contain '/' or surrounding whitespace")
        return self


class TrueNASConfig(StrictModel):
    enabled: bool | Literal["auto"] = "auto"
    host: str | None = None
    api_key: SecretStr | None = None
    verify_tls: bool = False
    pool_aliases: dict[str, str] = Field(default_factory=dict)
    cache_seconds: int = Field(30, ge=0)

    @field_validator("enabled", mode="before")
    @classmethod
    def parse_tristate(cls, value: Any) -> Any:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == "auto":
                return "auto"
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
            raise ValueError("must be auto, true or false")
        return value

    @model_validator(mode="after")
    def explicit_on_needs_key(self) -> Self:
        if self.enabled is True and self.api_key is None:
            raise ValueError("truenas.enabled is true but no API key is configured")
        return self

    @property
    def is_enabled(self) -> bool:
        if self.enabled == "auto":
            return self.api_key is not None
        return self.enabled


class UIConfig(StrictModel):
    dark_theme: bool = True
    # BCP 47 tag such as "es-ES" for dates in the UI; None uses the browser's own locale
    locale: str | None = None
    clock: Literal["24h", "12h"] = "24h"

    @field_validator("locale", mode="before")
    @classmethod
    def empty_means_browser_default(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


class HistoryConfig(StrictModel):
    keep_days: int = Field(40, ge=1)
    max_log_bytes: int = Field(50 * MIB, ge=MIB)


class Config(StrictModel):
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    rsync: RsyncConfig = Field(default_factory=RsyncConfig)
    folders: list[FolderConfig] = Field(default_factory=list)
    truenas: TrueNASConfig = Field(default_factory=TrueNASConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    # Paused from the UI until restart: not a file key, so a private attribute set by the loader
    _paused: frozenset[str] = PrivateAttr(default=frozenset())

    @model_validator(mode="after")
    def cross_checks(self) -> Self:
        seen: set[str] = set()
        for folder in self.folders:
            if folder.name in seen:
                raise ValueError(f"folder name '{folder.name}' is used more than once")
            seen.add(folder.name)
        if self.enabled_folders:
            missing = [key for key in ("host", "user") if getattr(self.remote, key) is None]
            if missing:
                raise ValueError(
                    "remote." + " and remote.".join(missing) + " are required to back up folders"
                )
        return self

    @property
    def enabled_folders(self) -> list[FolderConfig]:
        """The folders a run covers: enabled in the file and not paused from the UI."""
        return [f for f in self.folders if f.enabled and f.name not in self._paused]

    @property
    def paused_folders(self) -> frozenset[str]:
        return self._paused

    def set_paused(self, names: Iterable[str]) -> None:
        """Remember which folders the UI paused; names the file no longer has are dropped."""
        known = {folder.name for folder in self.folders}
        self._paused = frozenset(name for name in names if name in known)
