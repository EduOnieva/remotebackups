"""Folders paused from the UI, kept in the runtime directory until the container restarts.

A pause is deliberately short-lived: it lives outside /config so a redeploy never carries a
forgotten pause, and the service deletes the file at every start. `enabled: false` in
config.yaml is the permanent switch and the file never overrides it.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PausedFolders:
    def __init__(self, path: Path) -> None:
        self._path = path

    def names(self) -> frozenset[str]:
        """The paused folder names; an unreadable file counts as nothing paused."""
        if not self._path.exists():
            return frozenset()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            names = data["paused"]
            if not isinstance(names, list):
                raise TypeError("paused is not a list")
            return frozenset(str(name) for name in names)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Ignoring unreadable pause file %s: %s", self._path, exc)
            return frozenset()

    def set(self, folder_name: str, *, paused: bool) -> frozenset[str]:
        names = set(self.names())
        if paused:
            names.add(folder_name)
        else:
            names.discard(folder_name)
        if names:
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps({"paused": sorted(names)}), encoding="utf-8")
            tmp.replace(self._path)
        else:
            self._path.unlink(missing_ok=True)
        return frozenset(names)

    def clear(self) -> frozenset[str]:
        """Forget every pause; returns what was forgotten so the caller can say so."""
        names = self.names()
        self._path.unlink(missing_ok=True)
        return names
