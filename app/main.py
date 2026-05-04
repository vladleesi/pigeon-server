"""FastAPI application entrypoint for Pigeon."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .admin_setup import ensure_default_admin
from .cleanup import run_periodic_cleanup
from .config import get_settings
from .db import init_db
from .routers import (
    admin_auth,
    admin_export,
    admin_ui,
    chats,
    health,
    landing,
    links,
    me,
    ws,
)

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
    settings = get_settings()

    app = FastAPI(
        title="Pigeon Server",
        version="0.1.0",
        description="Link-only messenger backend API.",
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    app.include_router(health.router)
    app.include_router(landing.router)
    app.include_router(links.router)
    app.include_router(me.router)
    app.include_router(chats.router)
    app.include_router(ws.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_ui.router)
    app.include_router(admin_export.router)

    return app


app = create_app()
