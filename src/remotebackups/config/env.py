"""Environment variables: process settings plus overrides for `config.yaml`.

Precedence inside the environment: `FILE__<VAR>` (a path whose contents become the
value, the linuxserver convention for Docker secrets) beats a plain `<VAR>`.
"""

import shlex
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from remotebackups.config.schema import HostKeyPolicy

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
FILE_PREFIX = "FILE__"


class FileEnvSource(PydanticBaseSettingsSource):
    """Reads `FILE__<FIELD>` variables and substitutes the named file's contents."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        import os

        path = os.environ.get(FILE_PREFIX + field_name.upper())
        if path is None:
            return None, field_name, False
        try:
            value = Path(path).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise ValueError(
                f"{FILE_PREFIX}{field_name.upper()} points to an unreadable file: {exc}"
            ) from exc
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, _ = self.get_field_value(field, field_name)
            if value is not None:
                values[key] = value
        return values


class EnvSettings(BaseSettings):
    """Everything the container reads from its environment. Unset means "not given"."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # Process-level settings that are not part of config.yaml
    config_dir: Path = Path("/config")
    runtime_dir: Path = Path("/run/remotebackups")
    secrets_dir: Path = Path("/run/secrets")
    web_port: int = Field(8080, ge=0, le=65535)  # 0 lets the OS pick a port
    log_level: str = "INFO"
    tz: str = "Etc/UTC"

    # Overrides for config.yaml
    remote_host: str | None = None
    remote_user: str | None = None
    remote_port: int | None = None
    ssh_key: str | None = None
    ssh_password: SecretStr | None = None
    ssh_host_key_policy: HostKeyPolicy | None = None
    cron_schedule: str | None = None
    scheduled_rsync_timeout: int | None = None
    rsync_extra_options: str | None = None
    truenas_enabled: str | None = None
    truenas_api_key: SecretStr | None = None
    truenas_host: str | None = None
    truenas_verify_tls: bool | None = None
    truenas_pool_aliases: str | None = None
    history_keep_days: int | None = None
    ui_dark_theme: bool | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, FileEnvSource(settings_cls), env_settings)

    @field_validator("log_level")
    @classmethod
    def known_log_level(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(LOG_LEVELS)}")
        return upper

    @field_validator("tz")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("is not a known IANA timezone") from exc
        return value

    def config_overrides(self) -> dict[str, Any]:
        """The config.yaml fragment expressed by the environment, containing only set values."""
        sections: dict[str, dict[str, Any]] = {
            "remote": {
                "host": self.remote_host,
                "user": self.remote_user,
                "port": self.remote_port,
                "ssh_key": self.ssh_key,
                "password": self.ssh_password,
                "host_key_policy": self.ssh_host_key_policy,
            },
            "schedule": {
                "cron": self.cron_schedule,
                "rsync_timeout": self.scheduled_rsync_timeout,
            },
            "rsync": {
                "extra_options": (
                    shlex.split(self.rsync_extra_options)
                    if self.rsync_extra_options is not None
                    else None
                ),
            },
            "truenas": {
                "enabled": self.truenas_enabled,
                "api_key": self.truenas_api_key,
                "host": self.truenas_host,
                "verify_tls": self.truenas_verify_tls,
                "pool_aliases": (
                    parse_pool_aliases(self.truenas_pool_aliases)
                    if self.truenas_pool_aliases is not None
                    else None
                ),
            },
            "history": {"keep_days": self.history_keep_days},
            "ui": {"dark_theme": self.ui_dark_theme},
        }
        return {
            section: {key: value for key, value in values.items() if value is not None}
            for section, values in sections.items()
            if any(value is not None for value in values.values())
        }


def parse_pool_aliases(raw: str) -> dict[str, str]:
    """`pool:alias,pool:alias` → mapping. Malformed entries are an error, not a warning."""
    aliases: dict[str, str] = {}
    for entry in raw.split(","):
        if not entry.strip():
            continue
        pool_name, sep, alias = entry.partition(":")
        if not sep or not pool_name.strip() or not alias.strip():
            raise ValueError(f"TRUENAS_POOL_ALIASES entry '{entry.strip()}' is not 'pool:alias'")
        aliases[pool_name.strip()] = alias.strip()
    return aliases
