"""Parse the `--stats` block rsync prints at the end of a transfer."""

import re
from collections.abc import Iterable

from remotebackups.sync.models import RsyncStats

# rsync localises nothing but does insert thousands separators ("1,234,567")
_NUMBER = r"([\d,]+)"
_PATTERNS: dict[str, re.Pattern[str]] = {
    "files_total": re.compile(rf"^Number of files: {_NUMBER}"),
    "files_created": re.compile(rf"^Number of created files: {_NUMBER}"),
    "files_deleted": re.compile(rf"^Number of deleted files: {_NUMBER}"),
    # 3.1+ says "regular files transferred", older versions "files transferred"
    "files_transferred": re.compile(rf"^Number of (?:regular )?files transferred: {_NUMBER}"),
    "total_size": re.compile(rf"^Total file size: {_NUMBER} bytes"),
    "bytes_sent": re.compile(rf"^Total bytes sent: {_NUMBER}"),
    "bytes_received": re.compile(rf"^Total bytes received: {_NUMBER}"),
}
_SPEEDUP = re.compile(r"speedup is ([\d.,]+)")


def parse_stats(lines: Iterable[str]) -> RsyncStats | None:
    """The stats found in `lines`, or None when rsync never printed a stats block."""
    values: dict[str, int] = {}
    speedup: float | None = None
    for raw in lines:
        line = raw.strip()
        for field, pattern in _PATTERNS.items():
            match = pattern.match(line)
            if match:
                values[field] = int(match.group(1).replace(",", ""))
                break
        else:
            match = _SPEEDUP.search(line)
            if match:
                speedup = float(match.group(1).replace(",", ""))
    if not values:
        return None
    return RsyncStats(**values, speedup=speedup)
