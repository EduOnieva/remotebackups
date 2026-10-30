"""SSH credentials and options for the remote host.

Everything rsync's ssh needs is written to one ssh_config under the runtime directory,
so the same file can be used by hand for debugging: `ssh -F /run/remotebackups/ssh/config host`.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from remotebackups.config.schema import RemoteConfig
from remotebackups.paths import ConfigPaths

logger = logging.getLogger(__name__)

HOST_KEY_CHANGED = re.compile(
    r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed"
)


class SshSetupError(Exception):
    """A credential problem a human must fix; the message says how."""


@dataclass(frozen=True)
class SshRuntime:
    """What a run needs: the generated config and the secret, if any, for sshpass."""

    config_path: Path
    password: str | None

    @property
    def command(self) -> list[str]:
        return ["ssh", "-F", str(self.config_path)]


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str


class SshSetup:
    def __init__(self, paths: ConfigPaths, remote: RemoteConfig) -> None:
        self._paths = paths
        self._remote = remote

    @property
    def password(self) -> str | None:
        return self._remote.password.get_secret_value() if self._remote.password else None

    def resolve_key(self) -> Path | None:
        """The private key to use, or None when password auth is configured instead.

        Without SSH_KEY the key is expected at the secrets mount point, which is outside
        /config so that mounting it never leaves a placeholder file on the host.
        """
        if self._remote.ssh_key:
            candidate = Path(self._remote.ssh_key)
            if not candidate.is_absolute():
                candidate = self._paths.secrets_dir / candidate
            if not candidate.is_file():
                raise SshSetupError(f"SSH key not found: {candidate}")
            return candidate
        default = self._paths.default_ssh_key
        if default.is_file():
            return default
        if self.password is not None:
            return None
        raise SshSetupError(
            f"No SSH key at {default} and no SSH_PASSWORD set; mount your private key there "
            "read-only or point SSH_KEY at it"
        )

    def check_key(self, key: Path) -> None:
        """Fail early on a passphrase-protected or malformed key, before any run tries it."""
        result = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", str(key)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise SshSetupError(
                f"SSH key {key} cannot be used non-interactively "
                f"(passphrase-protected or invalid): {detail}"
            )

    def prepare(self) -> SshRuntime:
        """Copy the key with safe permissions and write the ssh_config for this configuration."""
        key = self.resolve_key()
        runtime_key: Path | None = None
        if key is not None:
            runtime_key = self._paths.runtime_ssh_dir / "key"
            # Bind-mounted keys often arrive world-readable or with another owner; ssh refuses
            # them, so every check and every run uses this private copy instead
            shutil.copyfile(key, runtime_key)
            runtime_key.chmod(0o600)
            self.check_key(runtime_key)
        self._paths.known_hosts.touch(mode=0o600, exist_ok=True)
        if self._remote.host_key:
            self._pin_host_key(self._remote.host_key)

        config_path = self._paths.runtime_ssh_dir / "config"
        config_path.write_text(self._render_config(runtime_key), encoding="utf-8")
        config_path.chmod(0o600)
        return SshRuntime(config_path=config_path, password=self.password)

    async def probe(self, runtime: SshRuntime, timeout_seconds: float = 20) -> ProbeResult:
        """Can we log in right now? Used at startup and shown in the UI, never fatal."""
        argv = [*runtime.command, f"{self._remote.user}@{self._remote.host}", "true"]
        env = minimal_env()
        if runtime.password is not None:
            argv = ["sshpass", "-e", *argv]
            env["SSHPASS"] = runtime.password
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            return ProbeResult(False, f"could not start ssh: {exc}")
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
        except TimeoutError:
            await _kill(process)
            return ProbeResult(
                False, f"no answer from {self._remote.host} within {timeout_seconds:.0f}s"
            )
        except asyncio.CancelledError:
            # Shutdown while probing: do not leave an ssh child behind
            await _kill(process)
            raise
        if process.returncode == 0:
            return ProbeResult(True, "ok")
        detail = stderr.decode("utf-8", "replace").strip().splitlines()
        return ProbeResult(False, explain_ssh_failure(detail, self._paths.known_hosts))

    def _render_config(self, key: Path | None) -> str:
        lines = [
            "# Generated by remotebackups at startup; edit config.yaml or the environment instead",
            "Host *",
            f"  Port {self._remote.port}",
            f"  User {self._remote.user}",
            f"  StrictHostKeyChecking {self._remote.host_key_policy.ssh_value}",
            f"  UserKnownHostsFile {self._paths.known_hosts}",
            "  HashKnownHosts no",
            "  ConnectTimeout 10",
            "  ServerAliveInterval 30",
            "  ServerAliveCountMax 3",
            "  LogLevel ERROR",
        ]
        if key is not None:
            lines += [
                f"  IdentityFile {key}",
                "  IdentitiesOnly yes",
                "  PasswordAuthentication no",
                "  BatchMode yes",
            ]
        else:
            # sshpass answers the prompt, so BatchMode stays off and keys are not tried first
            lines += [
                "  PubkeyAuthentication no",
                "  PasswordAuthentication yes",
                "  NumberOfPasswordPrompts 1",
            ]
        return "\n".join(lines) + "\n"

    def _pin_host_key(self, host_key: str) -> None:
        host = self._remote.host
        if host is None:
            raise SshSetupError("remote.host is required to pin a host key")
        marker = f"[{host}]:{self._remote.port}" if self._remote.port != 22 else host
        line = f"{marker} {host_key.strip()}\n"
        existing = self._paths.known_hosts.read_text(encoding="utf-8")
        if line not in existing:
            kept = [entry for entry in existing.splitlines() if not entry.startswith(marker + " ")]
            self._paths.known_hosts.write_text("\n".join([*kept, line.rstrip("\n")]) + "\n")


def explain_ssh_failure(stderr_lines: list[str], known_hosts: Path) -> str:
    """One sentence for a human, with the fix when the cause is a changed host key."""
    text = "\n".join(stderr_lines)
    if HOST_KEY_CHANGED.search(text):
        return (
            "the remote host key changed or is unknown under the current policy; "
            f"if the server was reinstalled, delete its line from {known_hosts}"
        )
    meaningful = [line for line in stderr_lines if line.strip()]
    return meaningful[-1] if meaningful else "ssh exited with an error and no message"


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
        await process.wait()


def minimal_env() -> dict[str, str]:
    """Children get only what ssh and rsync need, not the application's secrets."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    for name in ("HOME", "TZ", "LANG", "LC_ALL"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env
