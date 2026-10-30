"""Pauses set from the UI, kept in the runtime directory until the container restarts.

Two kinds: folders that runs skip, and the schedule itself, whose due runs are skipped.
A pause is deliberately short-lived: it lives outside /config so a redeploy never carries a
forgotten pause, and the service deletes the file at every start. `enabled: false` and
`schedule.cron` in config.yaml are the permanent switches and the file never overrides them.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PauseState:
    folders: frozenset[str] = frozenset()
    schedule: bool = False

    @property
    def empty(self) -> bool:
        return not self.folders and not self.schedule


class Pauses:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> PauseState:
        """What is paused; an unreadable file counts as nothing paused."""
        if not self._path.exists():
            return PauseState()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            folders = data.get("folders", [])
            if not isinstance(folders, list):
                raise TypeError("folders is not a list")
            return PauseState(frozenset(str(n) for n in folders), bool(data.get("schedule", False)))
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            logger.warning("Ignoring unreadable pause file %s: %s", self._path, exc)
            return PauseState()

    def folders(self) -> frozenset[str]:
        return self.read().folders

    def schedule(self) -> bool:
        return self.read().schedule

    def set_folder(self, folder_name: str, *, paused: bool) -> PauseState:
        state = self.read()
        folders = set(state.folders)
        if paused:
            folders.add(folder_name)
        else:
            folders.discard(folder_name)
        return self._write(PauseState(frozenset(folders), state.schedule))

    def set_schedule(self, *, paused: bool) -> PauseState:
        return self._write(PauseState(self.read().folders, paused))

    def clear(self) -> PauseState:
        """Forget every pause; returns what was forgotten so the caller can say so."""
        state = self.read()
        self._path.unlink(missing_ok=True)
        return state

    def _write(self, state: PauseState) -> PauseState:
        if state.empty:
            self._path.unlink(missing_ok=True)
            return state
        tmp = self._path.with_name(self._path.name + ".tmp")
        data = {"folders": sorted(state.folders), "schedule": state.schedule}
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self._path)
        return state
