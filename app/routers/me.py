"""Authenticated client profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import get_current_user
from ..models import User
from ..schemas import ChatInfo, MeResponse
from ..services import get_user_chats, load_chat_info, user_to_participant

router = APIRouter(prefix="/api/v1", tags=["me"])


@router.get("/me", response_model=MeResponse)
async def me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    chats = await get_user_chats(session, user)
    info: list[ChatInfo] = [await load_chat_info(session, c) for c in chats]
    return MeResponse(
        user=user_to_participant(user),
        chats=info,
    )
