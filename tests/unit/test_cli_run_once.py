import json
from pathlib import Path

import pytest

from remotebackups.cli import main
from remotebackups.paths import ConfigPaths
from tests.fakes.control import FakeBinaries

pytestmark = pytest.mark.usefixtures("fake_bin", "ssh_key")


@pytest.fixture
def configured(paths: ConfigPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "data" / "docs"
    source.mkdir(parents=True)
    paths.config_file.write_text(
        f"remote: {{host: 10.0.0.9, user: backup}}\n"
        f"folders:\n  - source: {source}\n    destination: /mnt/backup/docs\n"
    )
    return source


def test_run_once_completes_and_prints_a_summary(
    configured: Path, paths: ConfigPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run-once"]) == 0

    out = capsys.readouterr().out
    assert "started; log:" in out
    assert "  docs: ok" in out
    assert out.rstrip().endswith("Outcome: completed")
    assert len(list(paths.runs_dir.glob("*.json"))) == 1


def test_run_once_exit_code_reflects_a_failed_folder(
    configured: Path, fake_bin: FakeBinaries, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_bin.configure(rsync_mode="exit:23")
    assert main(["run-once", "--delete"]) == 1
    assert "docs: failed (rsync exit 23" in capsys.readouterr().out


def test_run_once_dry_run(configured: Path, fake_bin: FakeBinaries, tmp_path: Path) -> None:
    args_file = tmp_path / "rsync-args"
    fake_bin.configure(rsync_args_file=str(args_file))
    assert main(["run-once", "--dry-run"]) == 0
    assert "--dry-run" in json.loads(args_file.read_text().splitlines()[0])


def test_run_once_unknown_folder(configured: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run-once", "--folder", "nope"]) == 2
    assert "unknown or disabled folder(s): nope" in capsys.readouterr().err
