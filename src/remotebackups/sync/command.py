"""Turn a folder plus the run's choices into the exact rsync argv."""

import shlex
from dataclasses import dataclass, field

from remotebackups.config.schema import FolderConfig, RemoteConfig, RsyncConfig
from remotebackups.sync.models import RunOptions

OWNERSHIP_OFF = ("--no-owner", "--no-group")


@dataclass(frozen=True)
class RsyncCommand:
    argv: list[str]
    # Added to a minimal environment; sshpass reads the password from SSHPASS
    env: dict[str, str] = field(default_factory=dict)

    def display(self) -> str:
        """The command as a shell line for the run log, never including the environment."""
        return shlex.join(self.argv)


class RsyncCommandBuilder:
    def __init__(
        self,
        rsync: RsyncConfig,
        remote: RemoteConfig,
        ssh_command: list[str],
        password: str | None = None,
    ) -> None:
        self._rsync = rsync
        self._remote = remote
        self._ssh_command = ssh_command
        self._password = password

    def build(self, folder: FolderConfig, options: RunOptions) -> RsyncCommand:
        argv: list[str] = []
        env: dict[str, str] = {}
        if self._password is not None:
            argv += ["sshpass", "-e"]
            env["SSHPASS"] = self._password

        argv.append("rsync")
        argv += self._rsync.options
        if not self._rsync.preserve_ownership:
            argv += OWNERSHIP_OFF
        if options.delete and folder.allow_delete:
            argv.append("--delete")
        if options.rsync_timeout:
            argv.append(f"--timeout={options.rsync_timeout}")
        argv += [f"--exclude={pattern}" for pattern in (*self._rsync.excludes, *folder.excludes)]
        argv += self._rsync.extra_options
        argv += folder.extra_options
        # rsync splits -e on whitespace itself, so the ssh command must not contain quoted spaces
        argv += ["-e", " ".join(self._ssh_command)]
        # Trailing slashes: copy the folder's contents into the destination, not the folder itself
        argv.append(f"{folder.source}/")
        argv.append(f"{self._remote.user}@{self._remote.host}:{folder.destination}/")
        return RsyncCommand(argv=argv, env=env)
