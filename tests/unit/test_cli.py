import pytest

from remotebackups import __version__
from remotebackups.cli import main
from remotebackups.config.loader import EXAMPLE_TEXT
from remotebackups.paths import ConfigPaths


def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"remotebackups {__version__}"


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "check-config" in capsys.readouterr().out


def test_print_example_config_needs_no_config_dir(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["print-example-config"]) == 0
    assert capsys.readouterr().out == EXAMPLE_TEXT


def test_check_config_on_a_fresh_dir_creates_the_file_and_passes(
    paths: ConfigPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check-config"]) == 0
    assert paths.config_file.exists()
    assert capsys.readouterr().out.strip() == (
        "OK: 0 enabled folder(s) of 0, schedule 30 2 * * 1, TrueNAS off"
    )


def test_check_config_reports_problems_on_stderr_with_exit_2(
    paths: ConfigPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    paths.config_file.write_text("folders:\n  - source: /data/x\n    destination: /mnt/x\n")

    assert main(["check-config"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Configuration problems:" in captured.err
    assert "remote.host and remote.user are required" in captured.err


def test_bad_environment_is_reported_the_same_way(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WEB_PORT", "99999")

    assert main(["check-config"]) == 2
    assert "environment: web_port:" in capsys.readouterr().err


def test_unreadable_file_variable_is_reported(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FILE__TRUENAS_API_KEY", "/nonexistent/key")

    assert main(["check-config"]) == 2
    assert (
        "environment: FILE__TRUENAS_API_KEY points to an unreadable file" in capsys.readouterr().err
    )


def test_print_config_redacts(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REMOTE_HOST", "nas")
    monkeypatch.setenv("REMOTE_USER", "backup")
    monkeypatch.setenv("SSH_PASSWORD", "hunter2")

    assert main(["print-config", "--redact"]) == 0
    out = capsys.readouterr().out
    assert "host: nas" in out
    assert "hunter2" not in out


def test_run_reports_an_invalid_config_and_exits_2(
    paths: ConfigPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    paths.config_file.write_text("schedule: {cron: whenever}\n")

    assert main(["run"]) == 2
    assert "schedule.cron: is not a valid cron expression" in capsys.readouterr().err
