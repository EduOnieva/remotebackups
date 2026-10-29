"""Build the FastAPI application around a `Services` bundle."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from remotebackups.web import api, health, pages
from remotebackups.web.services import Services

STATIC_DIR = Path(__file__).parent / "static"


def create_app(services: Services, *, sse_poll_seconds: float = 0.5) -> FastAPI:
    app = FastAPI(
        title="remotebackups",
        version=services.version,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.services = services
    app.state.sse_poll_seconds = sse_poll_seconds
    app.include_router(health.router)
    app.include_router(api.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
