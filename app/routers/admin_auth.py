"""Administrator login and logout routes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..db import get_session
from ..deps import ADMIN_COOKIE_NAME, get_optional_admin
from ..models import Admin, AdminSession, LoginLimit
from ..security import create_admin_token, decode_admin_token, verify_password
from ..templates import templates

router = APIRouter(prefix="/admin", tags=["admin"])

_settings = get_settings()


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    admin: Admin | None = Depends(get_optional_admin),
) -> HTMLResponse:
    if admin is not None:
        return RedirectResponse(url="/admin/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.now(timezone.utc)
    await session.execute(text("BEGIN IMMEDIATE"))
    await session.execute(delete(LoginLimit).where(LoginLimit.expires_at <= now))
    allowed = True
    for name, limit in (("ip:" + (request.client.host if request.client else "unknown"),
                         _settings.login_attempts_per_minute),
                        ("user:" + username, _settings.login_attempts_per_minute),
                        ("global", _settings.login_attempts_per_minute * 10)):
        key = hmac.new(_settings.secret_key.encode(), name.encode(), hashlib.sha256).hexdigest()
        row = await session.get(LoginLimit, key)
        if row is None:
            if (await session.scalar(select(func.count(LoginLimit.key)))) >= 10000:
                allowed = False
                break
            row = LoginLimit(key=key, attempts=0, expires_at=now + timedelta(minutes=1))
            session.add(row)
        row.attempts += 1
        allowed = allowed and row.attempts <= limit
    await session.commit()
    if not allowed:
        return templates.TemplateResponse(request, "login.html",
            {"error": "Too many attempts. Try again in a minute."}, status_code=429,
            headers={"Retry-After": "60"})
    result = await session.execute(select(Admin).where(Admin.username == username))
    admin = result.scalar_one_or_none()
    if admin is None or not await run_in_threadpool(verify_password, password, admin.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=401,
        )

    if (await session.scalar(select(func.count(AdminSession.id)))) >= 1000:
        return templates.TemplateResponse(request, "login.html",
            {"error": "Session capacity reached. Try again later."}, status_code=429)
    admin.last_login_at = datetime.now(timezone.utc)
    sid = secrets.token_hex(16)
    session.add(AdminSession(id=sid, admin_id=admin.id,
                            expires_at=now + timedelta(
                                hours=_settings.admin_session_ttl_hours)))
    await session.commit()

    token = create_admin_token(admin.id, admin.username, sid)
    response = RedirectResponse(url="/admin/", status_code=303)
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        max_age=_settings.admin_session_ttl_hours * 3600,
        secure=request.url.scheme == "https" or _settings.public_url.startswith("https://"),
        path="/",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request, session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    import jwt
    try:
        payload = decode_admin_token(request.cookies.get(ADMIN_COOKIE_NAME, ""))
        await session.execute(delete(AdminSession).where(AdminSession.id == payload.get("sid", "")))
        await session.commit()
    except jwt.InvalidTokenError:
        pass
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return response
