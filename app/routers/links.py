"""Client-facing invite-link activation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import resolve_client_user
from ..models import User
from ..schemas import LinkActivateRequest, LinkActivateResponse, _decode_b64
from ..security import create_client_token
from ..services import activate_link, load_chat_info, user_to_participant

router = APIRouter(prefix="/api/v1/links", tags=["links"])


async def _current_user_if_any(
    session: AsyncSession,
    authorization: str | None,
) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return await resolve_client_user(session, token)


@router.post("/{token}/activate", response_model=LinkActivateResponse)
async def activate(
    token: str,
    payload: LinkActivateRequest,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> LinkActivateResponse:
    existing = await _current_user_if_any(session, authorization)

    pub_key = _decode_b64(payload.public_key)
    if existing is not None and pub_key != existing.public_key:
        raise HTTPException(
            status_code=409,
            detail="public_key mismatch with existing session",
        )

    user, chat, link = await activate_link(
        session=session,
        link_token=token,
        public_key=pub_key,
        display_name=payload.display_name,
        current_user=existing,
    )

    chat_info = await load_chat_info(session, chat)
    jwt_token = create_client_token(user.id, user.public_id, link.id)
    return LinkActivateResponse(
        token=jwt_token,
        user=user_to_participant(user),
        chat=chat_info,
    )
