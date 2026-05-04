"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
