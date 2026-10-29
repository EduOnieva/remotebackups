"""Drive the fake binaries in tests/fakes/bin.

The engine passes children a minimal environment, so the fakes cannot be steered through
`FAKE_*` variables there. Instead they read `$HOME/.remotebackups-fakes.json`; the fixture
points HOME at a temporary directory and this helper writes the file.
"""

import json
from pathlib import Path

CONTROL_FILE_NAME = ".remotebackups-fakes.json"


class FakeBinaries:
    def __init__(self, bin_dir: Path, home: Path) -> None:
        self.bin_dir = bin_dir
        self.home = home
        self._control = home / CONTROL_FILE_NAME

    def configure(self, **settings: str) -> None:
        """Merge settings such as rsync_mode="exit:23" into the control file."""
        current: dict[str, str] = {}
        if self._control.exists():
            current = json.loads(self._control.read_text())
        current.update(settings)
        self._control.write_text(json.dumps(current))
