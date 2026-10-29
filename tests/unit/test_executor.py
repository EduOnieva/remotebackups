import asyncio
import os
from collections.abc import Callable

import pytest

from remotebackups.sync.executor import ProcessRunner
from tests.fakes.control import FakeBinaries


def env_with(**extra: str) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], **extra}


async def test_streams_lines_and_returns_the_exit_code(fake_bin: FakeBinaries) -> None:
    lines: list[str] = []
    runner = ProcessRunner(lines.append)
    code = await runner.run(["rsync", "src/", "dst/"], env_with(FAKE_RSYNC_MODE="exit:23"))

    assert code == 23
    assert lines[0] == "sending incremental file list"
    assert any(line.startswith("Number of files:") for line in lines)


async def test_cancel_sends_sigterm_to_the_group(fake_bin: FakeBinaries) -> None:
    lines: list[str] = []
    runner = ProcessRunner(lines.append, grace_seconds=5)
    task = asyncio.create_task(runner.run(["rsync"], env_with(FAKE_RSYNC_MODE="hang")))
    await _wait_for(lambda: "still copying" in lines)

    await runner.cancel()

    assert await task == 20, "the fake exits 20 on SIGTERM like rsync does"


async def test_cancel_escalates_to_sigkill(fake_bin: FakeBinaries) -> None:
    lines: list[str] = []
    runner = ProcessRunner(lines.append, grace_seconds=0.3)
    task = asyncio.create_task(runner.run(["rsync"], env_with(FAKE_RSYNC_MODE="hang-ignore-term")))
    await _wait_for(lambda: "still copying" in lines)

    await runner.cancel()

    assert await task == -9


async def test_cancel_without_a_process_is_a_no_op(fake_bin: FakeBinaries) -> None:
    runner = ProcessRunner(lambda _: None)
    await runner.cancel()
    code = await runner.run(["rsync"], env_with())
    assert code == 0
    await runner.cancel()


async def _wait_for(condition: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)


pytestmark = pytest.mark.usefixtures("fake_bin")
