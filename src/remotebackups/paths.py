"""Where remotebackups keeps its files inside the container."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfigPaths:
    """The persistent `/config` volume layout plus the per-boot runtime directory."""

    config_dir: Path = Path("/config")
    runtime_dir: Path = Path("/run/remotebackups")

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.yaml"

    @property
    def example_file(self) -> Path:
        return self.config_dir / "config.example.yaml"

    @property
    def ssh_dir(self) -> Path:
        return self.config_dir / "ssh"

    @property
    def known_hosts(self) -> Path:
        return self.ssh_dir / "known_hosts"

    @property
    def runs_dir(self) -> Path:
        return self.config_dir / "runs"

    @property
    def runtime_ssh_dir(self) -> Path:
        return self.runtime_dir / "ssh"

    def ensure(self) -> None:
        """Create the directories the app writes to. The ssh ones are private to root."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(exist_ok=True)
        for private in (self.ssh_dir, self.runtime_ssh_dir):
            private.mkdir(parents=True, exist_ok=True)
            private.chmod(0o700)

    def config_dir_is_mounted(self) -> bool:
        """False means state lives in the container layer and dies with it."""
        return self.config_dir.is_mount()
