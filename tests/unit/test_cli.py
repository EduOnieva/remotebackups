import pytest

from remotebackups import __version__
from remotebackups.cli import main


def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"remotebackups {__version__}"


def test_no_arguments_is_a_no_op_for_now() -> None:
    assert main([]) == 0
