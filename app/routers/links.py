"""Client-facing invite-link activation."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..deps import resolve_client_user
from ..invite_security import require_secure_transport
from ..models import Link, User
from ..schemas import LinkActivateRequest, LinkActivateResponse, _decode_b64
from ..security import create_client_token
from ..services import activate_link, load_chat_info, user_to_participant
from .sessions import digest, issue

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
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> LinkActivateResponse:
    deadline = get_settings().legacy_token_deadline
    if (payload.session_credential is None and deadline is not None
            and datetime.now(timezone.utc) >= deadline.replace(tzinfo=timezone.utc)):
        raise HTTPException(410, "renewable session credential required")
    if payload.session_credential is not None:
        digest(payload.session_credential.get_secret_value())
    # SELECT FOR UPDATE is ignored by SQLite. Acquire its write reservation
    # before any auth/membership reads, also across multiple server processes.
    await session.execute(text("BEGIN IMMEDIATE"))
    link = await session.scalar(select(Link).where(Link.token == token))
    if payload.password is not None or (link is not None and link.password_hash is not None):
        require_secure_transport(request)
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
        password=payload.password.get_secret_value() if payload.password is not None else None,
        resume_credential=(
            payload.resume_credential.get_secret_value()
            if payload.resume_credential is not None else None
        ),
    )

    chat_info = await load_chat_info(session, chat)
    if payload.session_credential is not None:
        await session.commit()
        credentials = await issue(
            session, user, link.id, payload.session_credential.get_secret_value())
    else:
        credentials = {"token": create_client_token(user.id, user.public_id, link.id)}
    return LinkActivateResponse(
        **credentials,
        user=user_to_participant(user),
        chat=chat_info,
    )
