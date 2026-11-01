"""The single HTML page; everything dynamic comes from the API.

`custom.css` and `custom.js` under /config are loaded after the page's own files when they
exist, checked on every request so adding or editing one needs no restart.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from remotebackups.web.services import Services

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

CUSTOM_FILES = {"custom.css": "text/css", "custom.js": "text/javascript"}


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
            "custom_css": (svc.config_dir / "custom.css").is_file(),
            "custom_js": (svc.config_dir / "custom.js").is_file(),
        },
    )


@router.get("/custom.css")
@router.get("/custom.js")
async def custom_file(request: Request) -> FileResponse:
    """The user's own stylesheet or script from /config; never cached, so edits show on reload."""
    svc: Services = request.app.state.services
    name = request.url.path.lstrip("/")
    path = svc.config_dir / name
    if not path.is_file():
        raise HTTPException(404, f"no {name} in the config directory")
    return FileResponse(path, media_type=CUSTOM_FILES[name], headers={"Cache-Control": "no-cache"})
