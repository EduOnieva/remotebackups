import pytest
import yaml

from remotebackups.config import ConfigError, EnvSettings, dump_config, load_config
from remotebackups.config.loader import EXAMPLE_TEXT, ensure_config_files
from remotebackups.config.paused import PausedFolders
from remotebackups.paths import ConfigPaths

MINIMAL = """
remote: {host: 100.64.0.9, user: backup}
folders:
  - source: /data/docs
    destination: /mnt/backup/docs
"""


def test_example_is_valid_yaml_that_loads_as_an_empty_config(paths: ConfigPaths) -> None:
    assert ensure_config_files(paths) is True
    assert paths.config_file.read_text() == EXAMPLE_TEXT
    assert sorted(p.name for p in paths.config_dir.iterdir() if p.is_file()) == ["config.yaml"]

    config = load_config(paths, EnvSettings())
    assert config.folders == []
    assert config.schedule.cron == "30 2 * * 1"


def test_existing_config_is_kept(paths: ConfigPaths) -> None:
    paths.config_file.write_text(MINIMAL)

    assert ensure_config_files(paths) is False
    assert paths.config_file.read_text() == MINIMAL


def test_paused_folders_are_skipped_and_unknown_names_dropped(paths: ConfigPaths) -> None:
    paths.config_file.write_text(
        MINIMAL + "  - source: /data/photos\n    destination: /mnt/backup/photos\n"
    )
    store = PausedFolders(paths.paused_file)
    store.set("photos", paused=True)
    store.set("renamed-away", paused=True)

    config = load_config(paths, EnvSettings())

    assert config.paused_folders == {"photos"}
    assert [f.name for f in config.enabled_folders] == ["docs"]
    assert [f.name for f in config.folders] == ["docs", "photos"], "still listed"
    assert dump_config(config, redact=True).endswith(
        "# paused from the UI until the container restarts: photos\n"
    )
    assert "paused" not in config.model_dump(mode="json"), "not a key anyone can write"


def test_missing_file_means_defaults(paths: ConfigPaths) -> None:
    assert load_config(paths, EnvSettings()).folders == []


def test_file_values_are_loaded(paths: ConfigPaths) -> None:
    paths.config_file.write_text(MINIMAL)
    config = load_config(paths, EnvSettings())
    assert config.remote.host == "100.64.0.9"
    assert [f.name for f in config.folders] == ["docs"]


def test_environment_overrides_the_file(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths.config_file.write_text(MINIMAL)
    monkeypatch.setenv("REMOTE_HOST", "10.0.0.5")
    monkeypatch.setenv("CRON_SCHEDULE", "")

    config = load_config(paths, EnvSettings())
    assert config.remote.host == "10.0.0.5"
    assert config.remote.user == "backup", "untouched file values survive the overlay"
    assert config.schedule.cron is None


def test_invalid_yaml_is_reported_with_the_file_name(paths: ConfigPaths) -> None:
    paths.config_file.write_text("folders: [unclosed")
    with pytest.raises(ConfigError) as exc:
        load_config(paths, EnvSettings())
    assert exc.value.problems[0].startswith(f"{paths.config_file}: invalid YAML")


def test_non_mapping_top_level_is_reported(paths: ConfigPaths) -> None:
    paths.config_file.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_config(paths, EnvSettings())


def test_validation_problems_name_the_dotted_path(paths: ConfigPaths) -> None:
    paths.config_file.write_text(
        """
remote: {host: nas, user: backup}
schedule: {cron: whenever}
folders:
  - source: /data/ok
    destination: /mnt/ok
  - source: data/relative
    destination: /mnt/x
    colour: blue
"""
    )
    with pytest.raises(ConfigError) as exc:
        load_config(paths, EnvSettings())

    assert exc.value.problems == [
        "schedule.cron: is not a valid cron expression (got 'whenever')",
        "folders[1].source: source must be an absolute path (got 'data/relative')",
        "folders[1].colour: unknown key",
    ]


def test_missing_required_field_is_reported(paths: ConfigPaths) -> None:
    paths.config_file.write_text("folders:\n  - source: /data/x\n")
    with pytest.raises(ConfigError) as exc:
        load_config(paths, EnvSettings())
    assert "folders[0].destination: is required" in exc.value.problems


def test_dump_redacts_secrets_by_default_and_reveals_on_request(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths.config_file.write_text(MINIMAL)
    monkeypatch.setenv("TRUENAS_API_KEY", "1-topsecret")
    monkeypatch.setenv("SSH_HOST_KEY_POLICY", "strict")
    config = load_config(paths, EnvSettings())

    redacted = yaml.safe_load(dump_config(config, redact=True))
    assert redacted["truenas"]["api_key"] == "**********"
    assert redacted["remote"]["host_key_policy"] == "strict"
    assert "1-topsecret" not in dump_config(config, redact=True)

    revealed = yaml.safe_load(dump_config(config, redact=False))
    assert revealed["truenas"]["api_key"] == "1-topsecret"
    assert revealed["remote"]["host_key_policy"] == "strict"
    assert revealed["folders"][0]["name"] == "docs"
