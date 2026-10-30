import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from remotebackups.config.schema import Config, FolderConfig, RemoteConfig
from remotebackups.paths import ConfigPaths
from tests.fakes.clock import FakeClock
from tests.fakes.control import FakeBinaries

FAKE_BIN = Path(__file__).parent / "fakes" / "bin"

# Every variable EnvSettings reads; cleared so a developer's shell cannot leak into a test
ENV_VARS = (
    "CONFIG_DIR",
    "RUNTIME_DIR",
    "SECRETS_DIR",
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
    "UI_LOCALE",
    "UI_CLOCK",
)


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """configure_logging() installs a handler on the captured stderr; drop it after each test."""
    yield
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
        handler.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name in ENV_VARS or name.startswith(("FILE__", "FAKE_")):
            monkeypatch.delenv(name)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigPaths:
    """A throwaway /config and runtime dir, also exported as CONFIG_DIR for the CLI."""
    config_dir = tmp_path / "config"
    runtime_dir = tmp_path / "run"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("SECRETS_DIR", str(secrets_dir))
    config_paths = ConfigPaths(
        config_dir=config_dir, runtime_dir=runtime_dir, secrets_dir=secrets_dir
    )
    config_paths.ensure()
    return config_paths


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_bin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeBinaries:
    """Fake rsync/ssh/ssh-keygen/sshpass first on PATH, steered through a file under a temp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{FAKE_BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    return FakeBinaries(FAKE_BIN, home)


@pytest.fixture
def ssh_key(paths: ConfigPaths) -> Path:
    key = paths.default_ssh_key
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n")
    key.chmod(0o644)
    return key


@pytest.fixture
def make_config(tmp_path: Path) -> Callable[..., Config]:
    """A valid Config whose folder sources exist on disk; keyword overrides reach Config()."""

    def _make(folder_names: tuple[str, ...] = ("docs", "photos"), **overrides: object) -> Config:
        folders = []
        for name in folder_names:
            source = tmp_path / "data" / name
            source.mkdir(parents=True, exist_ok=True)
            folders.append(
                FolderConfig(name=name, source=str(source), destination=f"/mnt/backup/{name}")
            )
        values: dict[str, object] = {
            "remote": RemoteConfig(host="10.0.0.9", user="backup"),
            "folders": folders,
        }
        values.update(overrides)
        return Config.model_validate(values)

    return _make
