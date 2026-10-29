"""remotebackups: scheduled rsync backups of home-server folders to another host."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("remotebackups")
except PackageNotFoundError:  # pragma: no cover - source tree that was never installed
    __version__ = "0.0.0+unknown"
