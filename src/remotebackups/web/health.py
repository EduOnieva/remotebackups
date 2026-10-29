"""`/healthz` for the Docker HEALTHCHECK: 200 while the service can do its job."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from remotebackups.web.schemas import HealthResponse
from remotebackups.web.services import Services

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz(request: Request) -> JSONResponse:
    svc: Services = request.app.state.services
    reasons = []
    if not svc.scheduler_alive():
        reasons.append("scheduler is not running")
    # An unreachable remote is shown in the UI, not reported here: a sleeping NAS is normal
    body = HealthResponse(
        status="ok" if not reasons else "degraded",
        version=svc.version,
        reasons=reasons,
        next_run_at=svc.next_run_at(),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if not reasons else 503)
