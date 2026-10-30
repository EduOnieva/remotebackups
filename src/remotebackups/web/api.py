"""The JSON API under /api."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from remotebackups.config import ConfigError
from remotebackups.sync import (
    RunAlreadyActiveError,
    RunOptions,
    RunOutcome,
    RunState,
    RunTrigger,
    UnknownFolderError,
)
from remotebackups.truenas.service import TrueNASDetails
from remotebackups.web.schemas import (
    FolderInfo,
    RemoteInfo,
    RunAccepted,
    RunDetail,
    RunRequest,
    RunSummary,
    StatusResponse,
)
from remotebackups.web.services import RemoteStatusLike, Services
from remotebackups.web.sse import stream_run_log

router = APIRouter(prefix="/api")

MAX_LOG_CHUNK = 1024 * 1024


def services(request: Request) -> Services:
    result: Services = request.app.state.services
    return result


def same_origin_json(request: Request) -> None:
    """Refuse what an HTML form on another site can send: the CSRF guard of a UI without login.

    A form cannot set a JSON content type, and browsers mark cross-site requests with
    Sec-Fetch-Site. Scripts and curl add the header; the page's own fetch already does.
    """
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(403, "cross-site request refused")
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(403, "state changes need Content-Type: application/json")


mutating = [Depends(same_origin_json)]


def _remote_info(svc: Services, remote: RemoteStatusLike) -> RemoteInfo:
    config = svc.config_provider()
    return RemoteInfo(
        host=config.remote.host,
        user=config.remote.user,
        port=config.remote.port,
        auth="password" if config.remote.password else "key",
        host_key_policy=config.remote.host_key_policy.value,
        ok=remote.ok,
        message=remote.message,
        checked_at=remote.checked_at,
    )


@router.get("/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    svc = services(request)
    config = svc.config_provider()
    current = svc.engine.current
    return StatusResponse(
        state=svc.engine.state,
        current_run=RunSummary.from_record(current) if current else None,
        last_run=_last_finished(svc),
        next_run_at=svc.next_run_at(),
        schedule=config.schedule.cron,
        remote=_remote_info(svc, svc.remote_status()),
        truenas_enabled=svc.truenas.enabled,
        dark_theme=config.ui.dark_theme,
        locale=config.ui.locale,
        clock=config.ui.clock,
        version=svc.version,
        started_at=svc.started_at,
    )


@router.post("/remote/check", response_model=RemoteInfo, dependencies=mutating)
async def check_remote(request: Request) -> RemoteInfo:
    """Probe the SSH login now instead of waiting for the periodic check."""
    svc = services(request)
    return _remote_info(svc, await svc.probe_remote())


@router.get("/folders", response_model=list[FolderInfo])
async def folders(request: Request) -> list[FolderInfo]:
    config = services(request).config_provider()
    return [
        FolderInfo(
            name=f.name,
            source=f.source,
            destination=f.destination,
            enabled=f.enabled,
            allow_delete=f.allow_delete,
            source_exists=_exists(f.source),
        )
        for f in config.folders
    ]


@router.post("/runs", response_model=RunAccepted, status_code=202, dependencies=mutating)
async def start_run(request: Request, body: RunRequest | None = None) -> RunAccepted:
    svc = services(request)
    body = body or RunRequest()
    try:
        record = await svc.engine.start(
            RunTrigger.MANUAL, RunOptions(delete=body.delete, folders=body.folders)
        )
    except RunAlreadyActiveError as exc:
        raise HTTPException(409, f"run {exc.run_id} is already active") from exc
    except UnknownFolderError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(400, "configuration is invalid: " + "; ".join(exc.problems)) from exc
    return RunAccepted(run_id=record.id, state=RunState.RUNNING)


@router.post(
    "/runs/current/cancel", response_model=RunAccepted, status_code=202, dependencies=mutating
)
async def cancel_run(request: Request) -> RunAccepted:
    svc = services(request)
    current = svc.engine.current
    if current is None:
        raise HTTPException(404, "no run is active")
    await svc.engine.cancel("user")
    return RunAccepted(run_id=current.id, state=RunState.CANCELLING)


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 20
) -> list[RunSummary]:
    return [RunSummary.from_record(r) for r in services(request).history.list_runs(limit)]


@router.delete("/runs", status_code=204, response_class=Response, dependencies=mutating)
async def clear_runs(request: Request) -> Response:
    svc = services(request)
    if svc.engine.current is not None:
        raise HTTPException(409, "cannot clear history while a run is active")
    svc.history.clear()
    return Response(status_code=204)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(request: Request, run_id: str) -> RunDetail:
    record = services(request).history.get(run_id)
    if record is None:
        raise HTTPException(404, f"run {run_id} not found")
    return RunDetail.from_record(record)


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
async def get_run_log(
    request: Request,
    run_id: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    max_bytes: Annotated[int, Query(ge=1, le=MAX_LOG_CHUNK)] = 65536,
    tail: Annotated[int | None, Query(ge=1, le=MAX_LOG_CHUNK)] = None,
) -> PlainTextResponse:
    """A chunk of the log from `offset`, or with `tail` the last N bytes from a line boundary.

    `X-Log-Start` and `X-Log-Offset` are the byte range returned; `X-Log-Complete` says
    whether the run is over and the range reaches the end of the file.
    """
    svc = services(request)
    record = svc.history.get(run_id)
    if record is None:
        raise HTTPException(404, f"run {run_id} not found")
    path = svc.history.log_path(run_id)
    if tail is not None:
        start, chunk, size = _read_tail(path, tail)
    else:
        start = offset
        chunk, size = _read_chunk(path, offset, max_bytes)
    new_offset = start + len(chunk)
    complete = record.outcome is not RunOutcome.RUNNING and new_offset >= size
    return PlainTextResponse(
        chunk.decode("utf-8", "replace"),
        headers={
            "X-Log-Start": str(start),
            "X-Log-Offset": str(new_offset),
            "X-Log-Complete": str(complete).lower(),
        },
    )


@router.get("/runs/{run_id}/log/download")
async def download_run_log(request: Request, run_id: str) -> FileResponse:
    """The whole log file, however large, as a download."""
    svc = services(request)
    if svc.history.get(run_id) is None:
        raise HTTPException(404, f"run {run_id} not found")
    path = svc.history.log_path(run_id)
    if not path.exists():
        raise HTTPException(404, f"run {run_id} has no log")
    return FileResponse(path, media_type="text/plain", filename=f"{run_id}.log")


@router.get("/runs/{run_id}/log/stream")
async def stream_log(request: Request, run_id: str) -> StreamingResponse:
    svc = services(request)
    if svc.history.get(run_id) is None:
        raise HTTPException(404, f"run {run_id} not found")
    last_event_id = request.headers.get("Last-Event-ID", "0")
    start = int(last_event_id) if last_event_id.isdigit() else 0
    poll = float(request.app.state.sse_poll_seconds)
    return StreamingResponse(
        stream_run_log(svc.history, run_id, start, poll_seconds=poll),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/config")
async def effective_config(request: Request) -> dict[str, Any]:
    """The effective configuration with secrets masked."""
    result: dict[str, Any] = services(request).config_provider().model_dump(mode="json")
    return result


@router.get("/truenas", response_model=TrueNASDetails)
async def truenas_details(request: Request) -> TrueNASDetails:
    """Pools with usage and health, and the current alerts, for the UI card."""
    return await services(request).truenas.details()


@router.get("/pool_status")
async def pool_status(request: Request) -> dict[str, int | str]:
    """Kept shape-compatible with v1: dashboards read `sync_errors`, `alerts` and pool keys."""
    svc = services(request)
    last = _last_finished(svc)
    result: dict[str, int | str] = {"sync_errors": last.folders_failed if last else 0}
    result.update(await svc.truenas.pool_status())
    return result


def _last_finished(svc: Services) -> RunSummary | None:
    for record in svc.history.list_runs(limit=5):
        if record.outcome is not RunOutcome.RUNNING:
            return RunSummary.from_record(record)
    return None


def _exists(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_dir()


def _read_chunk(path: Any, offset: int, max_bytes: int) -> tuple[bytes, int]:
    if not path.exists():
        return b"", 0
    size: int = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(offset)
        data: bytes = handle.read(max_bytes)
    return data, size


def _read_tail(path: Any, tail: int) -> tuple[int, bytes, int]:
    """The last `tail` bytes, moved forward to the next line start so no line is cut."""
    if not path.exists():
        return 0, b"", 0
    size: int = path.stat().st_size
    start = max(0, size - tail)
    with path.open("rb") as handle:
        handle.seek(start)
        data: bytes = handle.read()
    if start > 0:
        newline = data.find(b"\n")
        if newline >= 0:
            start += newline + 1
            data = data[newline + 1 :]
    return start, data, size
