from remotebackups.config.schema import FolderConfig, RemoteConfig, RsyncConfig
from remotebackups.sync.command import RsyncCommandBuilder
from remotebackups.sync.models import RunOptions

SSH = ["ssh", "-F", "/run/remotebackups/ssh/config"]
REMOTE = RemoteConfig(host="10.0.0.9", user="backup")
FOLDER = FolderConfig(source="/data/docs/", destination="/mnt/backup/docs")


def build(options: RunOptions | None = None, **kwargs: object) -> list[str]:
    rsync = RsyncConfig(**{k: v for k, v in kwargs.items() if k in RsyncConfig.model_fields})
    folder = kwargs.get("folder", FOLDER)
    assert isinstance(folder, FolderConfig)
    password = kwargs.get("password")
    assert password is None or isinstance(password, str)
    return (
        RsyncCommandBuilder(rsync, REMOTE, SSH, password)
        .build(folder, options or RunOptions())
        .argv
    )


def test_default_manual_run() -> None:
    argv = build(excludes=["*.tmp"])
    assert argv == [
        "rsync",
        "-a", "-v", "-z", "--partial", "--stats",
        "--no-owner", "--no-group",
        "--exclude=*.tmp",
        "-e", "ssh -F /run/remotebackups/ssh/config",
        "/data/docs/",
        "backup@10.0.0.9:/mnt/backup/docs/",
    ]  # fmt: skip


def test_preserve_ownership_drops_the_no_owner_flags() -> None:
    argv = build(preserve_ownership=True, excludes=[])
    assert "--no-owner" not in argv
    assert "--no-group" not in argv


def test_delete_is_added_only_when_asked_and_allowed() -> None:
    assert "--delete" not in build(RunOptions(delete=False), excludes=[])
    assert "--delete" in build(RunOptions(delete=True), excludes=[])
    guarded = FolderConfig(source="/data/x", destination="/mnt/x", allow_delete=False)
    assert "--delete" not in build(RunOptions(delete=True), excludes=[], folder=guarded)


def test_timeout_is_added_only_for_a_positive_value() -> None:
    assert "--timeout=3600" in build(RunOptions(rsync_timeout=3600), excludes=[])
    assert not any(
        a.startswith("--timeout") for a in build(RunOptions(rsync_timeout=0), excludes=[])
    )
    assert not any(a.startswith("--timeout") for a in build(RunOptions(), excludes=[]))


def test_extra_options_and_folder_excludes_follow_the_global_ones() -> None:
    folder = FolderConfig(
        source="/data/x", destination="/mnt/x", excludes=["cache/"], extra_options=["--bwlimit=1m"]
    )
    argv = build(excludes=["*.tmp"], extra_options=["--info=progress2"], folder=folder)
    excludes = [a for a in argv if a.startswith("--exclude=")]
    assert excludes == ["--exclude=*.tmp", "--exclude=cache/"]
    assert argv.index("--info=progress2") < argv.index("--bwlimit=1m") < argv.index("-e")


def test_password_mode_wraps_in_sshpass_and_passes_the_secret_via_env() -> None:
    command = RsyncCommandBuilder(RsyncConfig(), REMOTE, SSH, "hunter2").build(FOLDER, RunOptions())
    assert command.argv[:3] == ["sshpass", "-e", "rsync"]
    assert command.env == {"SSHPASS": "hunter2"}
    assert "hunter2" not in command.display()


def test_display_is_a_shell_line() -> None:
    command = RsyncCommandBuilder(RsyncConfig(excludes=["a b"]), REMOTE, SSH).build(
        FOLDER, RunOptions()
    )
    assert "'--exclude=a b'" in command.display()
