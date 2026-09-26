"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for ORM models."""


_settings = get_settings()

engine = create_async_engine(
    _settings.db_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a database session."""

    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager for sessions outside FastAPI."""

    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables if they do not exist."""

    from . import models  # noqa: F401 — register models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight in-place migration for installations created before
        # invite revocation was tied to client sessions. Existing inactive
        # links are treated as revoked once, which safely invalidates legacy
        # JWTs that did not carry an invite id.
        columns = await conn.execute(text("PRAGMA table_info(links)"))
        column_names = {row[1] for row in columns}
        if "is_deleted" not in column_names:
            await conn.execute(text(
                "ALTER TABLE links ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "revoked_at" not in column_names:
            await conn.execute(text("ALTER TABLE links ADD COLUMN revoked_at DATETIME"))
            await conn.execute(
                text(
                    "UPDATE links SET revoked_at = CURRENT_TIMESTAMP "
                    "WHERE is_active = 0"
                )
            )
