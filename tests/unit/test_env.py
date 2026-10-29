from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from remotebackups.config.env import EnvSettings, parse_pool_aliases


def test_defaults_when_nothing_is_set() -> None:
    env = EnvSettings()
    assert env.config_dir == Path("/config")
    assert env.web_port == 8080
    assert env.log_level == "INFO"
    assert env.tz == "Etc/UTC"
    assert env.config_overrides() == {}


def test_overrides_only_contain_set_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_HOST", "nas.tailnet.ts.net")
    monkeypatch.setenv("REMOTE_USER", "backup")
    monkeypatch.setenv("SCHEDULED_RSYNC_TIMEOUT", "0")
    monkeypatch.setenv("HISTORY_KEEP_DAYS", "7")
    monkeypatch.setenv("UI_DARK_THEME", "false")

    assert EnvSettings().config_overrides() == {
        "remote": {"host": "nas.tailnet.ts.net", "user": "backup"},
        "schedule": {"rsync_timeout": 0},
        "history": {"keep_days": 7},
        "ui": {"dark_theme": False},
    }


def test_empty_cron_is_passed_through_so_it_disables_the_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRON_SCHEDULE", "")
    assert EnvSettings().config_overrides() == {"schedule": {"cron": ""}}


def test_extra_options_are_shell_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSYNC_EXTRA_OPTIONS", "--bwlimit=20m --exclude 'a b'")
    assert EnvSettings().config_overrides()["rsync"] == {
        "extra_options": ["--bwlimit=20m", "--exclude", "a b"]
    }


def test_truenas_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUENAS_API_KEY", "1-secret")
    monkeypatch.setenv("TRUENAS_POOL_ALIASES", "backup-disk-0:disk-0, backup-disk-1:disk-1")
    monkeypatch.setenv("TRUENAS_VERIFY_TLS", "true")

    truenas = EnvSettings().config_overrides()["truenas"]
    assert truenas["api_key"] == SecretStr("1-secret")
    assert truenas["pool_aliases"] == {"backup-disk-0": "disk-0", "backup-disk-1": "disk-1"}
    assert truenas["verify_tls"] is True


def test_file_variable_reads_the_secret_from_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "truenas_key"
    secret.write_text("1-from-file\n")
    monkeypatch.setenv("FILE__TRUENAS_API_KEY", str(secret))
    monkeypatch.setenv("TRUENAS_API_KEY", "1-from-env")

    assert EnvSettings().truenas_api_key == SecretStr("1-from-file")


def test_file_variable_pointing_nowhere_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILE__SSH_PASSWORD", "/nonexistent/secret")
    # Raised by the settings source itself, so it is a bare ValueError rather than a ValidationError
    with pytest.raises(ValueError, match="FILE__SSH_PASSWORD points to an unreadable file"):
        EnvSettings()


def test_invalid_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Mars/Olympus")
    with pytest.raises(ValidationError, match="not a known IANA timezone"):
        EnvSettings()


def test_log_level_is_normalised_and_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert EnvSettings().log_level == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ValidationError, match="must be one of"):
        EnvSettings()


@pytest.mark.parametrize("raw", ["no-colon", ":alias", "pool:"])
def test_malformed_pool_alias_is_an_error(raw: str) -> None:
    with pytest.raises(ValueError, match="is not 'pool:alias'"):
        parse_pool_aliases(raw)


def test_pool_aliases_tolerate_spaces_and_trailing_commas() -> None:
    assert parse_pool_aliases(" tank : disk-0 ,") == {"tank": "disk-0"}
