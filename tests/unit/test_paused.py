import json
import logging
from pathlib import Path

import pytest

from remotebackups.config.paused import Pauses


def test_nothing_paused_without_a_file(tmp_path: Path) -> None:
    state = Pauses(tmp_path / "pauses.json").read()
    assert state.folders == frozenset()
    assert state.schedule is False
    assert state.empty


def test_folders_round_trip_and_the_empty_state_removes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "pauses.json"
    store = Pauses(path)

    assert store.set_folder("music", paused=True).folders == {"music"}
    assert store.set_folder("tv", paused=True).folders == {"music", "tv"}
    assert json.loads(path.read_text()) == {"folders": ["music", "tv"], "schedule": False}
    assert Pauses(path).folders() == {"music", "tv"}, "another process reads the same"
    assert not list(tmp_path.glob("*.tmp"))

    assert store.set_folder("music", paused=False).folders == {"tv"}
    assert store.set_folder("tv", paused=False).empty
    assert not path.exists()


def test_schedule_flag_lives_beside_the_folders(tmp_path: Path) -> None:
    path = tmp_path / "pauses.json"
    store = Pauses(path)
    store.set_folder("music", paused=True)

    assert store.set_schedule(paused=True).schedule is True
    assert json.loads(path.read_text()) == {"folders": ["music"], "schedule": True}
    assert store.schedule() is True
    assert store.folders() == {"music"}, "pausing the schedule keeps the folder pauses"

    assert store.set_schedule(paused=False).schedule is False
    assert store.folders() == {"music"}


def test_clear_returns_what_it_forgot(tmp_path: Path) -> None:
    path = tmp_path / "pauses.json"
    store = Pauses(path)
    store.set_folder("music", paused=True)
    store.set_schedule(paused=True)
    forgotten = store.clear()
    assert forgotten.folders == {"music"}
    assert forgotten.schedule is True
    assert not path.exists()
    assert store.clear().empty


@pytest.mark.parametrize("content", ["not json", "[]", '{"folders": "music"}'])
def test_unreadable_file_means_nothing_paused(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "pauses.json"
    path.write_text(content)
    with caplog.at_level(logging.WARNING, logger="remotebackups.config.paused"):
        assert Pauses(path).read().empty
    assert "Ignoring unreadable pause file" in caplog.text
