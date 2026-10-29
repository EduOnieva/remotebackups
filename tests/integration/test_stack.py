"""End-to-end checks against the compose stack in compose/docker-compose.test.yml.

Runs inside the `tests` container: the app is reachable at APP_URL, the app's /config volume
is mounted at /appconfig, the source folders at /data and the sshd destination at /dest.
"""

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

APP_URL = os.environ.get("APP_URL", "http://app:8080")
APP_PASSWORD_URL = os.environ.get("APP_PASSWORD_URL", "http://app-password:8080")
APP_CONFIG = Path("/appconfig")
SOURCE = Path("/data")
DEST = Path("/dest")

pytestmark = pytest.mark.integration


def wait_until(condition: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.25)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def wait_for_app(url: str) -> httpx.Client:
    client = httpx.Client(base_url=url, timeout=10)

    def healthy() -> bool:
        try:
            return client.get("/healthz").status_code == 200
        except httpx.HTTPError:
            return False

    wait_until(healthy, 60, f"{url} to become healthy")
    return client


@dataclass
class Folder:
    name: str
    source: str
    destination: str
    extra_options: list[str] = field(default_factory=list)

    def as_yaml(self) -> str:
        text = f"  - name: {self.name}\n    source: {self.source}\n"
        text += f"    destination: {self.destination}\n"
        if self.extra_options:
            quoted = ", ".join(f"'{option}'" for option in self.extra_options)
            text += f"    extra_options: [{quoted}]\n"
        return text


def write_config(*folders: Folder) -> None:
    (APP_CONFIG / "config.yaml").write_text("folders:\n" + "".join(f.as_yaml() for f in folders))


def run_record(client: httpx.Client, run_id: str) -> dict[str, object]:
    record: dict[str, object] = client.get(f"/api/runs/{run_id}").json()
    return record


def outcome(client: httpx.Client, run_id: str) -> str:
    return str(run_record(client, run_id)["outcome"])


def run_and_wait(client: httpx.Client, body: dict[str, object] | None = None) -> dict[str, object]:
    response = client.post("/api/runs", json=body or {})
    assert response.status_code == 202, response.text
    run_id = str(response.json()["run_id"])
    wait_until(lambda: outcome(client, run_id) != "running", 120, f"run {run_id} to finish")
    return run_record(client, run_id)


@pytest.fixture(scope="module")
def app() -> Iterator[httpx.Client]:
    client = wait_for_app(APP_URL)
    yield client
    client.close()


@pytest.fixture(scope="module", autouse=True)
def seed_data() -> None:
    docs = SOURCE / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "hello.txt").write_text("hello\n")
    (docs / "nested").mkdir(exist_ok=True)
    (docs / "nested" / "deep.txt").write_text("deep\n")
    big = SOURCE / "big"
    big.mkdir(parents=True, exist_ok=True)
    with (big / "blob.bin").open("wb") as handle:
        # Fresh random bytes per MiB: a repeated block would compress away under rsync's zstd
        # and the bandwidth limit would then have almost nothing to slow down
        for _ in range(30):
            handle.write(os.urandom(1024 * 1024))


def test_first_start_wrote_the_example_and_a_config(app: httpx.Client) -> None:
    assert (APP_CONFIG / "config.example.yaml").exists()
    assert (APP_CONFIG / "config.yaml").exists()
    status = app.get("/api/status").json()
    assert status["state"] == "idle"
    assert status["schedule"] is None, "CRON_SCHEDULE is empty in the stack"
    assert status["remote"]["ok"] is True, status["remote"]


def test_manual_run_copies_the_folder(app: httpx.Client) -> None:
    write_config(Folder("docs", "/data/docs", "/mnt/backup/key/docs"))

    record = run_and_wait(app)

    assert record["outcome"] == "completed", record
    assert (DEST / "key" / "docs" / "hello.txt").read_text() == "hello\n"
    assert (DEST / "key" / "docs" / "nested" / "deep.txt").read_text() == "deep\n"
    folder = record["folders"][0]  # type: ignore[index]
    assert folder["stats"]["files_transferred"] == 2
    log = app.get(f"/api/runs/{record['id']}/log").text
    assert "--no-owner" in log and "--delete" not in log
    # Trust-on-first-use happened during the startup probe; the pin must be there
    assert "sshd" in (APP_CONFIG / "ssh" / "known_hosts").read_text()


def test_delete_run_removes_files_gone_from_the_source(app: httpx.Client) -> None:
    (SOURCE / "docs" / "nested" / "deep.txt").unlink()

    kept = run_and_wait(app)
    assert kept["outcome"] == "completed"
    assert (DEST / "key" / "docs" / "nested" / "deep.txt").exists(), "no --delete, file stays"

    deleted = run_and_wait(app, {"delete": True})
    assert deleted["outcome"] == "completed"
    assert deleted["delete"] is True
    assert not (DEST / "key" / "docs" / "nested" / "deep.txt").exists()


def test_stop_cancels_the_run_and_a_rerun_finishes(app: httpx.Client) -> None:
    # --bwlimit is in KB/s, so 30 MiB takes about a minute: plenty of time to stop it
    write_config(Folder("big", "/data/big", "/mnt/backup/key/big", ["--bwlimit=500"]))
    run_id = str(app.post("/api/runs", json={}).json()["run_id"])
    wait_until(
        lambda: (
            app.get("/api/status").json()["current_run"] is not None
            and "big" in app.get(f"/api/runs/{run_id}/log").text
        ),
        30,
        "the big folder to start copying",
    )
    time.sleep(2)

    cancelled_at = time.monotonic()
    response = app.post("/api/runs/current/cancel")
    assert response.status_code == 202
    wait_until(lambda: outcome(app, run_id) != "running", 15, "the cancelled run to close")
    record = run_record(app, run_id)
    assert record["outcome"] == "cancelled", record
    assert record["reason"] == "user"
    assert time.monotonic() - cancelled_at < 12, "rsync should go within the grace period"
    assert app.get("/api/status").json()["state"] == "idle"

    write_config(Folder("big", "/data/big", "/mnt/backup/key/big"))
    rerun = run_and_wait(app)
    assert rerun["outcome"] == "completed", rerun
    assert (DEST / "key" / "big" / "blob.bin").stat().st_size == 30 * 1024 * 1024


def test_second_start_during_a_run_is_refused(app: httpx.Client) -> None:
    write_config(Folder("big", "/data/big", "/mnt/backup/key/big-again", ["--bwlimit=500"]))
    first = app.post("/api/runs", json={})
    assert first.status_code == 202
    second = app.post("/api/runs", json={})
    assert second.status_code == 409
    app.post("/api/runs/current/cancel")
    wait_until(lambda: app.get("/api/status").json()["state"] == "idle", 15, "idle")


def test_pool_status_keeps_the_v1_shape_with_truenas_off(app: httpx.Client) -> None:
    body = app.get("/api/pool_status").json()
    assert body == {"sync_errors": 0, "truenas": "disabled"}


def test_history_and_logs_are_served(app: httpx.Client) -> None:
    runs = app.get("/api/runs").json()
    assert len(runs) >= 4
    outcomes = {r["outcome"] for r in runs}
    assert {"completed", "cancelled"} <= outcomes
    log = app.get(f"/api/runs/{runs[0]['id']}/log")
    assert log.status_code == 200
    assert log.headers["X-Log-Complete"] == "true"


def test_password_authentication_works_too() -> None:
    client = wait_for_app(APP_PASSWORD_URL)
    try:
        _check_password_instance(client)
    finally:
        client.close()


def _check_password_instance(client: httpx.Client) -> None:
    # This instance has its own /config volume: write the config through the API's view of it
    status = client.get("/api/status").json()
    assert status["remote"]["ok"] is True, status["remote"]
    Path("/appconfig-password/config.yaml").write_text(
        "folders:\n  - source: /data/docs\n    destination: /mnt/backup/password/docs\n"
    )
    record = run_and_wait(client)
    assert record["outcome"] == "completed", record
    assert (DEST / "password" / "docs" / "hello.txt").read_text() == "hello\n"
