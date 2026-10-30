from httpx import AsyncClient

from remotebackups.sync import RunOutcome
from remotebackups.truenas.models import AlertInfo, PoolInfo
from remotebackups.truenas.service import TrueNASDetails
from tests.web.conftest import Harness


async def test_status_when_idle_with_no_history(client: AsyncClient, harness: Harness) -> None:
    harness.remote.ok = None
    harness.remote.message = "not checked yet"
    harness.remote.checked_at = None
    response = await client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert body["current_run"] is None
    assert body["last_run"] is None
    assert body["next_run_at"] is None
    assert body["schedule"] == "30 2 * * 1"
    assert body["remote"] == {
        "host": "10.0.0.9",
        "user": "backup",
        "port": 22,
        "auth": "key",
        "host_key_policy": "tofu",
        "ok": None,
        "message": "not checked yet",
        "checked_at": None,
    }
    assert body["truenas_enabled"] is False
    assert body["dark_theme"] is True
    assert body["locale"] is None
    assert body["clock"] == "24h"
    assert body["version"] == "2.0.0-test"


async def test_status_during_a_run(client: AsyncClient, harness: Harness) -> None:
    harness.finished_run(RunOutcome.COMPLETED_WITH_ERRORS, failed=1)
    started = (await client.post("/api/runs", json={"delete": True})).json()

    body = (await client.get("/api/status")).json()
    assert body["state"] == "running"
    assert body["current_run"]["id"] == started["run_id"]
    assert body["current_run"]["delete"] is True
    assert body["current_run"]["current_folder"] == "docs"
    assert body["current_run"]["folders_total"] == 2
    assert body["last_run"]["outcome"] == "completed_with_errors"
    assert body["last_run"]["folders_failed"] == 1


async def test_check_remote_probes_now_and_returns_the_result(
    client: AsyncClient, harness: Harness
) -> None:
    harness.remote.ok = False
    harness.remote.message = "Connection refused"
    harness.remote.next_ok = True

    response = await client.post("/api/remote/check")

    assert response.status_code == 200
    assert harness.remote.probes == 1
    assert response.json()["ok"] is True
    assert (await client.get("/api/status")).json()["remote"]["ok"] is True


async def test_folders(client: AsyncClient) -> None:
    body = (await client.get("/api/folders")).json()
    assert [f["name"] for f in body] == ["docs", "photos"]
    assert body[0]["source_exists"] is True
    assert body[0]["allow_delete"] is True


async def test_start_run_accepted_then_conflict(client: AsyncClient, harness: Harness) -> None:
    first = await client.post("/api/runs", json={"delete": False, "folders": ["docs"]})
    assert first.status_code == 202
    assert first.json()["state"] == "running"
    assert harness.engine.starts[0].folders == ["docs"]

    second = await client.post("/api/runs")
    assert second.status_code == 409
    assert first.json()["run_id"] in second.json()["detail"]


async def test_start_run_without_body_uses_defaults(client: AsyncClient, harness: Harness) -> None:
    assert (await client.post("/api/runs")).status_code == 202
    assert harness.engine.starts[0].delete is False
    assert harness.engine.starts[0].folders is None


async def test_mutating_routes_refuse_what_a_cross_site_form_can_send(
    client: AsyncClient, harness: Harness
) -> None:
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert (await client.post("/api/runs", headers=form)).status_code == 403
    assert (await client.post("/api/runs/current/cancel", headers=form)).status_code == 403
    assert (await client.post("/api/remote/check", headers=form)).status_code == 403
    assert (await client.delete("/api/runs", headers=form)).status_code == 403
    # Same-origin JSON, as the page sends it, and a cross-site request with the right type
    assert (
        await client.post("/api/runs", headers={"Sec-Fetch-Site": "same-origin"})
    ).status_code == 202
    assert (
        await client.post("/api/runs/current/cancel", headers={"Sec-Fetch-Site": "cross-site"})
    ).status_code == 403
    assert harness.engine.starts and harness.engine.cancelled == []


async def test_start_run_with_unknown_folder_is_400(client: AsyncClient) -> None:
    response = await client.post("/api/runs", json={"folders": ["nope"]})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


async def test_cancel(client: AsyncClient, harness: Harness) -> None:
    assert (await client.post("/api/runs/current/cancel")).status_code == 404
    run_id = (await client.post("/api/runs")).json()["run_id"]

    response = await client.post("/api/runs/current/cancel")
    assert response.status_code == 202
    assert response.json() == {"run_id": run_id, "state": "cancelling"}
    assert harness.engine.cancelled == ["user"]


async def test_list_and_get_runs(client: AsyncClient, harness: Harness) -> None:
    older = harness.finished_run()
    newer = harness.finished_run(RunOutcome.FAILED, failed=2)

    listed = (await client.get("/api/runs?limit=1")).json()
    assert [r["id"] for r in listed] == [newer.id]
    assert listed[0]["folders_failed"] == 2
    assert listed[0]["duration_s"] == 0

    detail = (await client.get(f"/api/runs/{older.id}")).json()
    assert detail["id"] == older.id
    assert [f["status"] for f in detail["folders"]] == ["ok", "ok"]
    assert detail["log_lines_dropped"] == 0

    assert (await client.get("/api/runs/missing")).status_code == 404
    assert (await client.get("/api/runs?limit=0")).status_code == 422


async def test_log_chunks_with_offsets(client: AsyncClient, harness: Harness) -> None:
    run = harness.finished_run()

    first = await client.get(f"/api/runs/{run.id}/log?max_bytes=9")
    assert first.status_code == 200
    assert first.text == "line one\n"
    assert first.headers["X-Log-Offset"] == "9"
    assert first.headers["X-Log-Complete"] == "false"

    rest = await client.get(f"/api/runs/{run.id}/log?offset=9")
    assert rest.text == "line two\n"
    assert rest.headers["X-Log-Complete"] == "true"

    assert (await client.get("/api/runs/missing/log")).status_code == 404


async def test_run_ids_outside_the_generated_format_are_not_found(client: AsyncClient) -> None:
    for odd in ("missing", "20261030T083932Z-65d0.json", "..", "%2e%2e%2fconfig"):
        assert (await client.get(f"/api/runs/{odd}/log")).status_code == 404
        assert (await client.get(f"/api/runs/{odd}/log/download")).status_code == 404


async def test_log_tail_starts_at_a_line_boundary(client: AsyncClient, harness: Harness) -> None:
    run = harness.finished_run()  # log is "line one\nline two\n", 18 bytes

    response = await client.get(f"/api/runs/{run.id}/log?tail=12")
    assert response.text == "line two\n", "the cut inside 'line one' moves to the next line"
    assert response.headers["X-Log-Start"] == "9"
    assert response.headers["X-Log-Offset"] == "18"
    assert response.headers["X-Log-Complete"] == "true"

    whole = await client.get(f"/api/runs/{run.id}/log?tail=1000")
    assert whole.text == "line one\nline two\n"
    assert whole.headers["X-Log-Start"] == "0"


async def test_log_download_serves_the_whole_file(client: AsyncClient, harness: Harness) -> None:
    run = harness.finished_run()

    response = await client.get(f"/api/runs/{run.id}/log/download")
    assert response.status_code == 200
    assert response.text == "line one\nline two\n"
    assert response.headers["content-type"].startswith("text/plain")
    assert f'filename="{run.id}.log"' in response.headers["content-disposition"]

    assert (await client.get("/api/runs/missing/log/download")).status_code == 404


async def test_clear_history(client: AsyncClient, harness: Harness) -> None:
    harness.finished_run()
    assert (await client.delete("/api/runs")).status_code == 204
    assert (await client.get("/api/runs")).json() == []

    await client.post("/api/runs")
    assert (await client.delete("/api/runs")).status_code == 409


async def test_config_is_redacted(client: AsyncClient, harness: Harness) -> None:
    harness.config.truenas.api_key = __import__("pydantic").SecretStr("1-secret")
    body = (await client.get("/api/config")).json()
    assert body["folders"][0]["name"] == "docs"
    assert body["truenas"]["api_key"] == "**********"


async def test_pool_status_compat_shapes(client: AsyncClient, harness: Harness) -> None:
    assert (await client.get("/api/pool_status")).json() == {
        "sync_errors": 0,
        "truenas": "disabled",
    }

    harness.finished_run(RunOutcome.COMPLETED_WITH_ERRORS, failed=1)
    harness.truenas.enabled = True
    harness.truenas.answer = {"alerts": 2, "disk-0": "3.42 TB", "disk-1": "0.5 TB"}
    assert (await client.get("/api/pool_status")).json() == {
        "sync_errors": 1,
        "alerts": 2,
        "disk-0": "3.42 TB",
        "disk-1": "0.5 TB",
    }

    harness.truenas.answer = {"error": "Failed to connect to TrueNAS: unreachable"}
    body = (await client.get("/api/pool_status")).json()
    assert body["sync_errors"] == 1
    assert body["error"].startswith("Failed to connect")


async def test_truenas_details_endpoint(client: AsyncClient, harness: Harness) -> None:
    assert (await client.get("/api/truenas")).json()["enabled"] is False

    harness.truenas.enabled = True
    harness.truenas.detail = TrueNASDetails(
        enabled=True,
        fetched_at=harness.clock.now(),
        pools=[
            PoolInfo(
                name="backup-disk-0",
                alias="disk-0",
                status="ONLINE",
                healthy=True,
                size=1000,
                allocated=850,
                free=150,
            )
        ],
        alerts=[AlertInfo(level="CRITICAL", text="Pool faulted")],
    )
    body = (await client.get("/api/truenas")).json()
    assert body["enabled"] is True
    assert body["pools"][0]["alias"] == "disk-0"
    assert body["pools"][0]["used_pct"] == 85.0
    assert body["alerts"][0]["rank"] == 5
    assert body["fetched_at"] is not None


async def test_healthz(client: AsyncClient, harness: Harness) -> None:
    ok = await client.get("/healthz")
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"

    harness.scheduler_alive = False
    degraded = await client.get("/healthz")
    assert degraded.status_code == 503
    assert degraded.json() == {
        "status": "degraded",
        "version": "2.0.0-test",
        "reasons": ["scheduler is not running"],
        "next_run_at": None,
    }


async def test_index_page_carries_the_theme(client: AsyncClient, harness: Harness) -> None:
    page = await client.get("/")
    assert page.status_code == 200
    assert 'data-theme="dark"' in page.text
    assert "backup@10.0.0.9" in page.text

    harness.config.ui.dark_theme = False
    assert 'data-theme="light"' in (await client.get("/")).text


async def test_static_assets_are_served(client: AsyncClient) -> None:
    assert (await client.get("/static/app.js")).status_code == 200
    assert (await client.get("/static/app.css")).status_code == 200
    assert (await client.get("/static/favicon.svg")).status_code == 200
