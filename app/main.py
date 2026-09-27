"""FastAPI application entrypoint for Sideword."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .admin_setup import ensure_default_admin
from .cleanup import run_periodic_cleanup
from .db import init_db
from .guards import SecurityGuards
from .routers import (
    admin_auth,
    admin_export,
    admin_ui,
    chats,
    client,
    health,
    landing,
    links,
    me,
    sessions,
    ws,
)
from .version import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await ensure_default_admin()
    cleanup_task = asyncio.create_task(run_periodic_cleanup())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sideword Server",
        version=__version__,
        description="Link-only messenger backend API.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(RequestValidationError)
    async def private_validation_errors(request: Request, exc: RequestValidationError):
        # Pydantic's default errors echo request inputs, including credentials.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]})

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    app.include_router(health.router)
    app.include_router(landing.router)
    app.include_router(links.router)
    app.include_router(me.router)
    app.include_router(sessions.router)
    app.include_router(chats.router)
    app.include_router(ws.router)
    app.include_router(client.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_ui.router)
    app.include_router(admin_export.router)

    app.add_middleware(SecurityGuards)
    return app


app = create_app()
