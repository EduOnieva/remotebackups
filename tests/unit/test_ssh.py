import json
import stat
from pathlib import Path

import pytest

from remotebackups.config.schema import RemoteConfig
from remotebackups.paths import ConfigPaths
from remotebackups.sync.ssh import SshSetup, SshSetupError, explain_ssh_failure, minimal_env
from tests.fakes.control import FakeBinaries


def remote(**overrides: object) -> RemoteConfig:
    return RemoteConfig.model_validate({"host": "10.0.0.9", "user": "backup", **overrides})


class TestResolveKey:
    def test_single_key_is_auto_detected(self, paths: ConfigPaths, ssh_key: Path) -> None:
        (paths.ssh_dir / "id_ed25519.pub").write_text("pub")
        paths.known_hosts.touch()
        assert SshSetup(paths, remote()).resolve_key() == ssh_key

    def test_several_keys_need_an_explicit_choice(self, paths: ConfigPaths, ssh_key: Path) -> None:
        (paths.ssh_dir / "id_rsa").write_text("other")
        with pytest.raises(
            SshSetupError, match=r"Several keys .*id_ed25519, id_rsa.*; set SSH_KEY"
        ):
            SshSetup(paths, remote()).resolve_key()

    def test_explicit_name_is_looked_up_in_the_ssh_dir(
        self, paths: ConfigPaths, ssh_key: Path
    ) -> None:
        assert SshSetup(paths, remote(ssh_key="id_ed25519")).resolve_key() == ssh_key

    def test_explicit_absolute_path(self, paths: ConfigPaths, tmp_path: Path) -> None:
        key = tmp_path / "elsewhere.key"
        key.write_text("k")
        assert SshSetup(paths, remote(ssh_key=str(key))).resolve_key() == key

    def test_missing_explicit_key_is_an_error(self, paths: ConfigPaths) -> None:
        with pytest.raises(SshSetupError, match="SSH key not found"):
            SshSetup(paths, remote(ssh_key="nope")).resolve_key()

    def test_no_key_and_no_password_is_an_error(self, paths: ConfigPaths) -> None:
        with pytest.raises(SshSetupError, match=r"No SSH key .* and no SSH_PASSWORD"):
            SshSetup(paths, remote()).resolve_key()

    def test_password_without_key_is_fine(self, paths: ConfigPaths) -> None:
        assert SshSetup(paths, remote(password="hunter2")).resolve_key() is None


class TestPrepare:
    def test_key_mode_writes_config_and_a_private_copy(
        self, paths: ConfigPaths, ssh_key: Path, fake_bin: FakeBinaries
    ) -> None:
        runtime = SshSetup(paths, remote(port=2222)).prepare()

        copied = paths.runtime_ssh_dir / "key"
        assert copied.read_text() == ssh_key.read_text()
        assert stat.S_IMODE(copied.stat().st_mode) == 0o600
        assert runtime.password is None
        assert runtime.command == ["ssh", "-F", str(runtime.config_path)]
        config = runtime.config_path.read_text()
        assert "  Port 2222\n" in config
        assert "  User backup\n" in config
        assert "  StrictHostKeyChecking accept-new\n" in config
        assert f"  UserKnownHostsFile {paths.known_hosts}\n" in config
        assert f"  IdentityFile {copied}\n" in config
        assert "  BatchMode yes\n" in config
        assert paths.known_hosts.exists()

    def test_password_mode_disables_keys_and_batch_mode(
        self, paths: ConfigPaths, fake_bin: FakeBinaries
    ) -> None:
        runtime = SshSetup(paths, remote(password="hunter2")).prepare()
        config = runtime.config_path.read_text()
        assert runtime.password == "hunter2"
        assert "IdentityFile" not in config
        assert "BatchMode" not in config
        assert "  PubkeyAuthentication no\n" in config
        assert "  PasswordAuthentication yes\n" in config

    def test_passphrase_protected_key_is_rejected(
        self,
        paths: ConfigPaths,
        ssh_key: Path,
        fake_bin: FakeBinaries,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_bin.configure(ssh_keygen_mode="passphrase")
        with pytest.raises(SshSetupError, match="passphrase-protected or invalid"):
            SshSetup(paths, remote()).prepare()

    def test_pinned_host_key_is_written_and_policy_is_strict(
        self, paths: ConfigPaths, ssh_key: Path, fake_bin: FakeBinaries
    ) -> None:
        paths.known_hosts.write_text("10.0.0.9 ssh-ed25519 OLDKEY\nother.host ssh-rsa KEEP\n")
        runtime = SshSetup(paths, remote(host_key="ssh-ed25519 NEWKEY")).prepare()

        assert (
            paths.known_hosts.read_text()
            == "other.host ssh-rsa KEEP\n10.0.0.9 ssh-ed25519 NEWKEY\n"
        )
        assert "  StrictHostKeyChecking yes\n" in runtime.config_path.read_text()

    def test_pinned_host_key_uses_bracket_syntax_for_custom_ports(
        self, paths: ConfigPaths, ssh_key: Path, fake_bin: FakeBinaries
    ) -> None:
        SshSetup(paths, remote(port=2222, host_key="ssh-ed25519 K")).prepare()
        assert paths.known_hosts.read_text() == "[10.0.0.9]:2222 ssh-ed25519 K\n"


class TestProbe:
    async def test_success(
        self,
        paths: ConfigPaths,
        ssh_key: Path,
        fake_bin: FakeBinaries,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        args_file = tmp_path / "ssh-args"
        fake_bin.configure(ssh_args_file=str(args_file))
        setup = SshSetup(paths, remote())
        result = await setup.probe(setup.prepare())

        assert result.ok
        argv = json.loads(args_file.read_text().splitlines()[0])
        assert argv[1:] == ["-F", str(paths.runtime_ssh_dir / "config"), "backup@10.0.0.9", "true"]

    async def test_changed_host_key_is_explained(
        self,
        paths: ConfigPaths,
        ssh_key: Path,
        fake_bin: FakeBinaries,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_bin.configure(ssh_mode="hostkey")
        setup = SshSetup(paths, remote())
        result = await setup.probe(setup.prepare())

        assert not result.ok
        assert "host key changed" in result.message
        assert str(paths.known_hosts) in result.message

    async def test_other_failures_quote_the_last_ssh_line(
        self,
        paths: ConfigPaths,
        ssh_key: Path,
        fake_bin: FakeBinaries,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_bin.configure(ssh_mode="refused")
        setup = SshSetup(paths, remote())
        result = await setup.probe(setup.prepare())
        assert result.message == "ssh: connect to host 10.0.0.9 port 22: Connection refused"

    async def test_timeout(
        self,
        paths: ConfigPaths,
        ssh_key: Path,
        fake_bin: FakeBinaries,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_bin.configure(ssh_mode="hang")
        setup = SshSetup(paths, remote())
        result = await setup.probe(setup.prepare(), timeout_seconds=0.3)
        assert result.message == "no answer from 10.0.0.9 within 0s"

    async def test_password_mode_goes_through_sshpass(
        self,
        paths: ConfigPaths,
        fake_bin: FakeBinaries,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        args_file = tmp_path / "sshpass-args"
        fake_bin.configure(sshpass_args_file=str(args_file))
        setup = SshSetup(paths, remote(password="hunter2"))
        result = await setup.probe(setup.prepare())
        assert result.ok
        assert json.loads(args_file.read_text())[1:3] == ["-e", "ssh"]


def test_explain_without_output() -> None:
    assert explain_ssh_failure([], Path("/kh")) == "ssh exited with an error and no message"


def test_minimal_env_passes_only_what_ssh_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUENAS_API_KEY", "secret")
    monkeypatch.setenv("TZ", "Europe/Madrid")
    env = minimal_env()
    assert "TRUENAS_API_KEY" not in env
    assert env["TZ"] == "Europe/Madrid"
    assert "PATH" in env
