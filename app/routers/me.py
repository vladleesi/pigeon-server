"""Authenticated client profile endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import get_current_user
from ..models import Link, User
from ..schemas import ChatInfo, MeResponse
from ..security import decode_client_token
from ..services import get_user_chats, load_chat_info, user_to_participant

router = APIRouter(prefix="/api/v1", tags=["me"])


@router.get("/me", response_model=MeResponse)
async def me(
    authorization: str = Header(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    chats = await get_user_chats(session, user)
    info: list[ChatInfo] = [await load_chat_info(session, c) for c in chats]
    payload = decode_client_token(authorization.split(" ", 1)[1].strip())
    link = await session.get(Link, int(payload["lid"]))
    expiry = link.expires_at if link else None
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        expiry = min(expiry, datetime.fromtimestamp(payload["exp"], timezone.utc))
    return MeResponse(
        user=user_to_participant(user),
        chats=info,
        access_expires_at=expiry,
        server_time=datetime.now(timezone.utc),
    )
