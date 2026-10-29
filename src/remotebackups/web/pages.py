"""The single HTML page; everything dynamic comes from the API."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from remotebackups.web.services import Services

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    svc: Services = request.app.state.services
    config = svc.config_provider()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "theme": "dark" if config.ui.dark_theme else "light",
            "version": svc.version,
            "remote": f"{config.remote.user}@{config.remote.host}"
            if config.remote.host
            else "not configured",
        },
    )
