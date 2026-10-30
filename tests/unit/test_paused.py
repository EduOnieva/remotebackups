import json
import logging
from pathlib import Path

import pytest

from remotebackups.config.paused import PausedFolders


def test_nothing_paused_without_a_file(tmp_path: Path) -> None:
    assert PausedFolders(tmp_path / "paused.json").names() == frozenset()


def test_set_and_unset_round_trip_and_the_empty_set_removes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "paused.json"
    store = PausedFolders(path)

    assert store.set("music", paused=True) == {"music"}
    assert store.set("tv", paused=True) == {"music", "tv"}
    assert json.loads(path.read_text()) == {"paused": ["music", "tv"]}
    assert PausedFolders(path).names() == {"music", "tv"}, "another process reads the same"
    assert not list(tmp_path.glob("*.tmp"))

    assert store.set("music", paused=False) == {"tv"}
    assert store.set("tv", paused=False) == frozenset()
    assert not path.exists()


def test_clear_returns_what_it_forgot(tmp_path: Path) -> None:
    path = tmp_path / "paused.json"
    store = PausedFolders(path)
    store.set("music", paused=True)
    assert store.clear() == {"music"}
    assert not path.exists()
    assert store.clear() == frozenset()


@pytest.mark.parametrize("content", ["not json", '{"other": 1}', '{"paused": "music"}'])
def test_unreadable_file_means_nothing_paused(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "paused.json"
    path.write_text(content)
    with caplog.at_level(logging.WARNING, logger="remotebackups.config.paused"):
        assert PausedFolders(path).names() == frozenset()
    assert "Ignoring unreadable pause file" in caplog.text
