import os
from pathlib import Path

import pytest

from remotebackups.paths import ConfigPaths

# Every variable EnvSettings reads; cleared so a developer's shell cannot leak into a test
ENV_VARS = (
    "CONFIG_DIR",
    "RUNTIME_DIR",
    "WEB_PORT",
    "LOG_LEVEL",
    "TZ",
    "REMOTE_HOST",
    "REMOTE_USER",
    "REMOTE_PORT",
    "SSH_KEY",
    "SSH_PASSWORD",
    "SSH_HOST_KEY_POLICY",
    "CRON_SCHEDULE",
    "SCHEDULED_RSYNC_TIMEOUT",
    "RSYNC_EXTRA_OPTIONS",
    "TRUENAS_ENABLED",
    "TRUENAS_API_KEY",
    "TRUENAS_HOST",
    "TRUENAS_VERIFY_TLS",
    "TRUENAS_POOL_ALIASES",
    "HISTORY_KEEP_DAYS",
    "UI_DARK_THEME",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name in ENV_VARS or name.startswith("FILE__"):
            monkeypatch.delenv(name)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigPaths:
    """A throwaway /config and runtime dir, also exported as CONFIG_DIR for the CLI."""
    config_dir = tmp_path / "config"
    runtime_dir = tmp_path / "run"
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("RUNTIME_DIR", str(runtime_dir))
    config_paths = ConfigPaths(config_dir=config_dir, runtime_dir=runtime_dir)
    config_paths.ensure()
    return config_paths
