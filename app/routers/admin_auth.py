"""Administrator login and logout routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..deps import ADMIN_COOKIE_NAME, get_optional_admin
from ..models import Admin
from ..security import create_admin_token, verify_password
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
    result = await session.execute(select(Admin).where(Admin.username == username))
    admin = result.scalar_one_or_none()
    if admin is None or not verify_password(password, admin.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=401,
        )

    admin.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    token = create_admin_token(admin.id, admin.username)
    response = RedirectResponse(url="/admin/", status_code=303)
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        max_age=_settings.admin_session_ttl_hours * 3600,
        secure=_settings.public_url.startswith("https"),
        path="/",
    )
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return response
