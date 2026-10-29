from pathlib import Path

from remotebackups.sync.log import CAP_MESSAGE, RunLog
from tests.fakes.clock import FakeClock


def test_events_are_stamped_and_output_is_raw(tmp_path: Path, clock: FakeClock) -> None:
    log = RunLog(tmp_path / "run.log", max_bytes=10_000, now=clock.now)
    log.event("INFO", "started")
    log.output("file.bin\n")
    log.close()

    lines = (tmp_path / "run.log").read_text().splitlines()
    assert lines[0].endswith(" [INFO] started")
    assert lines[1] == "file.bin"


def test_cap_drops_output_but_never_events(tmp_path: Path, clock: FakeClock) -> None:
    log = RunLog(tmp_path / "run.log", max_bytes=40, now=clock.now)
    for index in range(10):
        log.output(f"line-{index}-xxxxxxxxxxxxxx")
    log.event("INFO", "finished")
    log.close()

    text = (tmp_path / "run.log").read_text()
    assert text.count("line-") == 1
    assert text.count(CAP_MESSAGE) == 1
    assert "[INFO] finished" in text
    assert log.lines_dropped == 9
