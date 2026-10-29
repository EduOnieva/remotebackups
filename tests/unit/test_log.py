import asyncio
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
    assert CAP_MESSAGE in text
    assert text.count(CAP_MESSAGE) == 1
    assert "[INFO] finished" in text
    assert log.lines_dropped == 9


async def test_wait_for_change_wakes_on_write_and_times_out_otherwise(
    tmp_path: Path, clock: FakeClock
) -> None:
    log = RunLog(tmp_path / "run.log", max_bytes=10_000, now=clock.now)

    assert await log.wait_for_change(max_wait=0.05) is False

    waiter = asyncio.create_task(log.wait_for_change(max_wait=5))
    await asyncio.sleep(0)
    log.output("new data")
    assert await waiter is True
    log.close()
