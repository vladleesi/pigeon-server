"""FastAPI dependencies: client and admin authentication."""

from __future__ import annotations

from datetime import datetime, timezone

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import Admin, User
from .security import decode_admin_token, decode_client_token


ADMIN_COOKIE_NAME = "pigeon_admin_session"


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_client_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc

    if payload.get("typ") != "client":
        raise HTTPException(status_code=401, detail="wrong token type")

    try:
        user_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid token subject") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="user not found")

    user.last_seen_at = datetime.now(timezone.utc)
    await session.commit()
    return user


async def get_current_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
    pigeon_admin_session: str | None = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> Admin:
    token = pigeon_admin_session
    if token is None:
        auth = request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="admin auth required")
    try:
        payload = decode_admin_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid session") from exc

    if payload.get("typ") != "admin":
        raise HTTPException(status_code=401, detail="wrong token type")

    try:
        admin_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid subject") from exc

    admin = await session.get(Admin, admin_id)
    if admin is None:
        raise HTTPException(status_code=401, detail="admin not found")
    return admin


async def get_optional_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
    pigeon_admin_session: str | None = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> Admin | None:
    if not pigeon_admin_session:
        return None
    try:
        return await get_current_admin(
            request=request,
            session=session,
            pigeon_admin_session=pigeon_admin_session,
        )
    except HTTPException:
        return None


def require_admin_ui(request: Request) -> None:
    """Placeholder dependency for UI routes (real guard uses redirects)."""

    # Documentation-only stub.
    _ = request
