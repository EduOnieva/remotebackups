from remotebackups.sync.exit_codes import PARTIAL_TRANSFER_CODES, describe_exit


def test_known_codes_have_the_manual_wording() -> None:
    assert describe_exit(0) == "success"
    assert describe_exit(23) == "partial transfer due to error"
    assert describe_exit(255) == "ssh could not connect or authenticate"


def test_unknown_code_is_still_described() -> None:
    assert describe_exit(99) == "unknown rsync exit code 99"


def test_negative_codes_name_the_signal() -> None:
    assert describe_exit(-9) == "killed by SIGKILL"
    assert describe_exit(-15) == "killed by SIGTERM"
    assert describe_exit(-200) == "killed by signal 200"


def test_partial_codes() -> None:
    assert {23, 24} == PARTIAL_TRANSFER_CODES
