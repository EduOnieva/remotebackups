import asyncio

from httpx import AsyncClient

from remotebackups.sync import RunOutcome
from tests.web.conftest import Harness


def parse_events(raw: str) -> list[tuple[str, str, str | None]]:
    """(event, data, id) tuples; keep-alive comments are dropped."""
    events = []
    for block in raw.strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            key, _, value = line.partition(": ")
            fields[key] = fields[key] + "\n" + value if key in fields else value
        if "event" in fields:
            events.append((fields["event"], fields.get("data", ""), fields.get("id")))
    return events


async def test_stream_replays_a_finished_log_then_ends(
    client: AsyncClient, harness: Harness
) -> None:
    run = harness.finished_run()
    response = await client.get(f"/api/runs/{run.id}/log/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert parse_events(response.text) == [
        ("line", "line one", "9"),
        ("line", "line two", "18"),
        ("state", "completed", None),
        ("end", "completed", None),
    ]


async def test_stream_resumes_from_last_event_id(client: AsyncClient, harness: Harness) -> None:
    run = harness.finished_run()
    response = await client.get(f"/api/runs/{run.id}/log/stream", headers={"Last-Event-ID": "9"})
    assert [e for e in parse_events(response.text) if e[0] == "line"] == [
        ("line", "line two", "18")
    ]


async def test_stream_follows_a_running_log_until_it_finishes(
    client: AsyncClient, harness: Harness
) -> None:
    run_id = (await client.post("/api/runs")).json()["run_id"]
    log_path = harness.history.log_path(run_id)

    async def writer() -> None:
        await asyncio.sleep(0.05)
        with log_path.open("a") as handle:
            handle.write("copying a\n")
        await asyncio.sleep(0.05)
        with log_path.open("a") as handle:
            handle.write("partial")  # no newline yet: must not be emitted
        await asyncio.sleep(0.05)
        with log_path.open("a") as handle:
            handle.write(" line\n")
        record = harness.history.get(run_id)
        assert record is not None
        record.outcome = RunOutcome.CANCELLED
        harness.history.save(record)
        harness.engine.current = None

    task = asyncio.create_task(writer())
    response = await client.get(f"/api/runs/{run_id}/log/stream")
    await task

    events = parse_events(response.text)
    assert [e[1] for e in events if e[0] == "line"] == ["started", "copying a", "partial line"]
    assert events[1] == ("state", "running", None)
    assert events[-2:] == [("state", "cancelled", None), ("end", "cancelled", None)]


async def test_stream_for_unknown_run_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/runs/missing/log/stream")).status_code == 404
