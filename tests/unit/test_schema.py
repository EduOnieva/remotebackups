import pytest
from pydantic import SecretStr, ValidationError

from remotebackups.config.schema import (
    Config,
    FolderConfig,
    HostKeyPolicy,
    RemoteConfig,
    RsyncConfig,
    ScheduleConfig,
    TrueNASConfig,
)


def folder(**overrides: object) -> dict[str, object]:
    return {"source": "/data/docs", "destination": "/mnt/backup/docs", **overrides}


def remote() -> dict[str, str]:
    return {"host": "100.64.0.9", "user": "backup"}


class TestFolder:
    def test_name_defaults_to_last_path_segment(self) -> None:
        assert FolderConfig(**folder()).name == "docs"

    def test_explicit_name_wins(self) -> None:
        assert FolderConfig(**folder(name="documents")).name == "documents"

    def test_trailing_slash_is_normalised(self) -> None:
        parsed = FolderConfig(**folder(source="/data/docs/", destination="/mnt/backup/docs//"))
        assert (parsed.source, parsed.destination) == ("/data/docs", "/mnt/backup/docs")

    @pytest.mark.parametrize("bad", ["data/docs", "./docs", ""])
    def test_relative_source_is_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError, match="source must be an absolute path"):
            FolderConfig(**folder(source=bad))

    def test_parent_segments_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"destination must not contain '\.\.'"):
            FolderConfig(**folder(destination="/mnt/../etc"))

    def test_root_source_needs_an_explicit_name(self) -> None:
        with pytest.raises(ValidationError, match="name is required when source is '/'"):
            FolderConfig(**folder(source="/"))

    def test_name_with_slash_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not contain '/'"):
            FolderConfig(**folder(name="a/b"))

    def test_per_run_options_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="'--delete' is decided per run"):
            FolderConfig(**folder(extra_options=["--delete"]))

    def test_unknown_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            FolderConfig(**folder(sources="/typo"))


class TestRsync:
    def test_defaults(self) -> None:
        parsed = RsyncConfig()
        assert parsed.options == ["-a", "-v", "-z", "--partial", "--stats"]
        assert parsed.preserve_ownership is False
        assert "lost+found" in parsed.excludes

    @pytest.mark.parametrize("option", ["--delete", "--delete-after", "--timeout=60"])
    def test_run_level_options_are_rejected_in_base_options(self, option: str) -> None:
        with pytest.raises(ValidationError, match="decided per run"):
            RsyncConfig(options=["-a", option])


class TestSchedule:
    def test_default_is_weekly(self) -> None:
        assert ScheduleConfig().cron == "30 2 * * 1"

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_empty_disables(self, empty: str | None) -> None:
        assert ScheduleConfig(cron=empty).cron is None

    def test_invalid_cron_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a valid cron expression"):
            ScheduleConfig(cron="every monday")

    def test_negative_timeout_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleConfig(rsync_timeout=-1)


class TestRemote:
    def test_pinned_host_key_forces_strict(self) -> None:
        parsed = RemoteConfig(host_key="ssh-ed25519 AAAAC3 test", host_key_policy="tofu")
        assert parsed.host_key_policy is HostKeyPolicy.STRICT

    @pytest.mark.parametrize(
        ("policy", "ssh_value"),
        [("tofu", "accept-new"), ("strict", "yes"), ("insecure", "no")],
    )
    def test_policy_maps_to_ssh_option(self, policy: str, ssh_value: str) -> None:
        assert HostKeyPolicy(policy).ssh_value == ssh_value

    def test_password_is_a_secret(self) -> None:
        parsed = RemoteConfig(password="hunter2")
        assert isinstance(parsed.password, SecretStr)
        assert "hunter2" not in repr(parsed)


class TestTrueNAS:
    def test_auto_is_off_without_key(self) -> None:
        assert TrueNASConfig().is_enabled is False

    def test_auto_is_on_with_key(self) -> None:
        assert TrueNASConfig(api_key="1-abc").is_enabled is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", "off"])
    def test_explicit_off_beats_key(self, raw: str) -> None:
        assert TrueNASConfig(enabled=raw, api_key="1-abc").is_enabled is False

    def test_explicit_on_without_key_is_an_error(self) -> None:
        with pytest.raises(ValidationError, match="no API key is configured"):
            TrueNASConfig(enabled="true")

    def test_garbage_tristate_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be auto, true or false"):
            TrueNASConfig(enabled="maybe")


class TestConfig:
    def test_empty_config_is_valid_and_has_no_folders(self) -> None:
        parsed = Config()
        assert parsed.folders == []
        assert parsed.ui.dark_theme is True
        assert parsed.history.keep_days == 40

    def test_folders_require_remote_host_and_user(self) -> None:
        with pytest.raises(ValidationError, match=r"remote\.host and remote\.user are required"):
            Config(folders=[folder()])

    def test_only_enabled_folders_require_a_remote(self) -> None:
        parsed = Config(folders=[folder(enabled=False)])
        assert parsed.enabled_folders == []

    def test_duplicate_names_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="'docs' is used more than once"):
            Config(remote=remote(), folders=[folder(), folder(destination="/mnt/other")])

    def test_complete_config(self) -> None:
        parsed = Config(
            remote=remote(),
            folders=[folder(), folder(source="/data/photos", destination="/mnt/backup/photos")],
        )
        assert [f.name for f in parsed.enabled_folders] == ["docs", "photos"]
