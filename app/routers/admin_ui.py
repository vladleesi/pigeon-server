"""Admin HTML UI routes."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import get_session
from ..deps import get_current_admin, get_optional_admin
from ..models import (
    Admin,
    Chat,
    ChatMember,
    ChatType,
    Link,
    LinkType,
    PendingMessage,
    User,
)
from ..security import generate_link_token, public_key_fingerprint
from ..templates import templates

router = APIRouter(prefix="/admin", tags=["admin-ui"])

_settings = get_settings()


def _require(admin: Admin | None) -> Admin:
    if admin is None:
        raise HTTPException(
            status_code=303,
            detail="redirect",
            headers={"Location": "/admin/login"},
        )
    return admin


async def _redirect_if_anonymous(admin: Admin | None) -> Admin | RedirectResponse:
    if admin is None:
        return RedirectResponse(url="/admin/login", status_code=303)
    return admin


def _link_url(link: Link) -> str:
    base = _settings.public_url.rstrip("/")
    return f"{base}/l/{link.token}"


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    admin: Admin | None = Depends(get_optional_admin),
    session: AsyncSession = Depends(get_session),
):
    if admin is None:
        return RedirectResponse(url="/admin/login", status_code=303)

    users_count = (await session.execute(select(func.count(User.id)))).scalar_one()
    active_links = (
        await session.execute(select(func.count(Link.id)).where(Link.is_active == True))  # noqa: E712
    ).scalar_one()
    chats_count = (await session.execute(select(func.count(Chat.id)))).scalar_one()
    pending_msgs = (
        await session.execute(select(func.count(PendingMessage.id)))
    ).scalar_one()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "admin": admin,
            "stats": {
                "users": users_count,
                "links_active": active_links,
                "chats": chats_count,
                "pending_messages": pending_msgs,
            },
        },
    )


@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    admin: Admin | None = Depends(get_optional_admin),
    session: AsyncSession = Depends(get_session),
):
    if admin is None:
        return RedirectResponse(url="/admin/login", status_code=303)

    result = await session.execute(
        select(User)
        .options(selectinload(User.memberships))
        .order_by(User.created_at.desc())
    )
    users = list(result.scalars().all())
    rows = []
    for u in users:
        rows.append(
            {
                "id": u.id,
                "public_id": u.public_id,
                "display_name": u.display_name,
                "fingerprint": public_key_fingerprint(u.public_key),
                "created_at": u.created_at,
                "last_seen_at": u.last_seen_at,
                "is_active": u.is_active,
                "chats": [m.chat_id for m in u.memberships],
            }
        )
    return templates.TemplateResponse(
        request,
        "users.html",
        {"admin": admin, "users": rows},
    )


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: int,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    user.is_active = False
    await session.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/activate")
async def activate_user(
    user_id: int,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    user.is_active = True
    await session.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/links", response_class=HTMLResponse)
async def links_list(
    request: Request,
    admin: Admin | None = Depends(get_optional_admin),
    session: AsyncSession = Depends(get_session),
):
    if admin is None:
        return RedirectResponse(url="/admin/login", status_code=303)

    result = await session.execute(select(Link).order_by(Link.created_at.desc()))
    items = list(result.scalars().all())
    rows = [
        {
            "id": link.id,
            "token": link.token,
            "link_type": link.link_type.value,
            "chat_id": link.chat_id,
            "max_uses": link.max_uses,
            "uses_count": link.uses_count,
            "is_active": link.is_active and (
                link.expires_at is None
                or (link.expires_at if link.expires_at.tzinfo else link.expires_at.replace(tzinfo=timezone.utc))
                > datetime.now(timezone.utc)
            ),
            "note": link.note,
            "created_at": link.created_at,
            "expires_at": link.expires_at,
            "url": _link_url(link),
        }
        for link in items
    ]
    return templates.TemplateResponse(
        request,
        "links.html",
        {"admin": admin, "links": rows, "public_url": _settings.public_url},
    )


@router.post("/links")
async def create_link(
    request: Request,
    link_type: str = Form(...),
    note: str | None = Form(default=None),
    expires_in_hours: int | None = Form(default=None),
    group_title: str | None = Form(default=None),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if link_type not in ("personal", "group"):
        raise HTTPException(status_code=400, detail="invalid link type")
    lt = LinkType(link_type)

    expires_at = None
    if expires_in_hours:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)

    link = Link(
        token=generate_link_token(),
        link_type=lt,
        max_uses=2 if lt is LinkType.personal else 0,
        uses_count=0,
        is_active=True,
        note=(note or None),
        expires_at=expires_at,
    )

    if lt is LinkType.group:
        chat = Chat(chat_type=ChatType.group, title=(group_title or None))
        session.add(chat)
        await session.flush()
        link.chat_id = chat.id

    session.add(link)
    await session.commit()
    return RedirectResponse(url="/admin/links", status_code=303)


@router.post("/links/{link_id}/revoke")
async def revoke_link(
    link_id: int,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    link = await session.get(Link, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    link.is_active = False
    await session.commit()
    return RedirectResponse(url="/admin/links", status_code=303)


@router.post("/links/{link_id}/reactivate")
async def reactivate_link(
    link_id: int,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    link = await session.get(Link, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    # Reactivate only links that still have spare capacity.
    if link.max_uses and link.uses_count >= link.max_uses:
        raise HTTPException(status_code=400, detail="link fully used; create a new one")
    link.is_active = True
    await session.commit()
    return RedirectResponse(url="/admin/links", status_code=303)


@router.get("/chats", response_class=HTMLResponse)
async def chats_list(
    request: Request,
    admin: Admin | None = Depends(get_optional_admin),
    session: AsyncSession = Depends(get_session),
):
    if admin is None:
        return RedirectResponse(url="/admin/login", status_code=303)

    result = await session.execute(
        select(Chat)
        .options(selectinload(Chat.members).selectinload(ChatMember.user))
        .order_by(Chat.created_at.desc())
    )
    chats = list(result.scalars().all())

    pending_counts_result = await session.execute(
        select(PendingMessage.chat_id, func.count(PendingMessage.id))
        .group_by(PendingMessage.chat_id)
    )
    pending_by_chat = dict(pending_counts_result.all())

    rows = [
        {
            "id": c.id,
            "chat_type": c.chat_type.value,
            "title": c.title,
            "created_at": c.created_at,
            "members": [m.user.public_id for m in c.members if m.user is not None],
            "pending": pending_by_chat.get(c.id, 0),
        }
        for c in chats
    ]
    return templates.TemplateResponse(
        request,
        "chats.html",
        {"admin": admin, "chats": rows},
    )


@router.post("/chats/{chat_id}/purge")
async def purge_chat(
    chat_id: int,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    from sqlalchemy import delete

    await session.execute(
        delete(PendingMessage).where(PendingMessage.chat_id == chat_id)
    )
    await session.commit()
    return RedirectResponse(url="/admin/chats", status_code=303)


@router.get("/export-ui", response_class=HTMLResponse)
async def export_ui(
    request: Request,
    admin: Admin | None = Depends(get_optional_admin),
):
    if admin is None:
        return RedirectResponse(url="/admin/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "export.html",
        {"admin": admin},
    )
