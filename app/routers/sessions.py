"""Opt-in renewable sessions with hashed credentials and bounded retry grace."""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..deps import get_current_user
from ..models import ClientSession, Link, RefreshUse, User
from ..security import create_client_token, decode_client_token

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def digest(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise HTTPException(422, "credential must encode 32 random bytes as unpadded base64url")
    return hashlib.sha256(value.encode()).hexdigest()


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def response(record):
    now = datetime.now(timezone.utc)
    return {
        "token": create_client_token(record.user_id, record.public_id, record.link_id, record.id),
        "session_id": record.id,
        "access_expires_at": min(utc(record.expires_at), now + timedelta(
            minutes=get_settings().client_access_minutes)),
        "session_expires_at": utc(record.expires_at),
    }


async def issue(session, user, link_id, credential):
    hashed = digest(credential)
    await session.execute(text("BEGIN IMMEDIATE"))
    previous = await session.scalar(select(ClientSession).where(
        ClientSession.refresh_hash == hashed))
    now = datetime.now(timezone.utc)
    if previous:
        if (previous.user_id != user.id or previous.public_id != user.public_id
                or previous.link_id != link_id or previous.revoked
                or utc(previous.expires_at) <= now):
            raise HTTPException(401, "session unavailable")
        return response(previous)
    if await session.get(RefreshUse, hashed):
        raise HTTPException(409, "credential already rotated; use saved refresh state")
    count = await session.scalar(select(func.count(ClientSession.id)).where(
        ClientSession.user_id == user.id, ClientSession.expires_at > now,
    ))
    total = await session.scalar(select(func.count(ClientSession.id)))
    if count >= 20 or total >= 10000:
        raise HTTPException(429, "session capacity reached")
    record = ClientSession(id=secrets.token_hex(16), user_id=user.id, public_id=user.public_id,
                           link_id=link_id, refresh_hash=hashed, created_at=now,
                           expires_at=now + timedelta(days=get_settings().client_session_days),
                           revoked=False)
    session.add(record)
    await session.commit()
    return response(record)


class Bootstrap(BaseModel):
    credential: SecretStr


class Refresh(BaseModel):
    credential: SecretStr
    next_credential: SecretStr


@router.post("")
async def bootstrap(payload: Bootstrap, authorization: str = Header(...),
                    user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    claims = decode_client_token(authorization.split(" ", 1)[1])
    if "sid" in claims:
        raise HTTPException(409, "session already renewable")
    return await issue(session, user, int(claims["lid"]), payload.credential.get_secret_value())


@router.post("/refresh")
async def refresh(payload: Refresh, session: AsyncSession = Depends(get_session)):
    old = digest(payload.credential.get_secret_value())
    new = digest(payload.next_credential.get_secret_value())
    if old == new:
        raise HTTPException(422, "rotation requires a fresh credential")
    await session.execute(text("BEGIN IMMEDIATE"))
    now = datetime.now(timezone.utc)
    used = await session.get(RefreshUse, old)
    record = (await session.get(ClientSession, used.session_id) if used else
              await session.scalar(select(ClientSession).where(ClientSession.refresh_hash == old)))
    if record is None or record.revoked or utc(record.expires_at) <= now:
        raise HTTPException(401, "session unavailable")
    user = await session.get(User, record.user_id)
    link = await session.get(Link, record.link_id)
    if (user is None or not user.is_active or user.public_id != record.public_id
            or link is None or link.is_deleted or link.revoked_at is not None
            or (link.expires_at is not None and utc(link.expires_at) <= now)):
        raise HTTPException(401, "session unavailable")
    if used:
        if (used.next_hash != new or record.refresh_hash != new
                or utc(used.retry_until) < now):
            record.revoked = True
            await session.commit()
            raise HTTPException(401, "refresh replay detected; session revoked")
        return response(record)
    if (await session.get(RefreshUse, new) or await session.scalar(
            select(ClientSession.id).where(ClientSession.refresh_hash == new))):
        raise HTTPException(409, "choose a fresh credential")
    # Retain used digests until session expiry; cap rotation state without evicting
    # replay evidence. At most one normal rotation per access-token interval.
    uses = await session.scalar(select(func.count(RefreshUse.token_hash)).where(
        RefreshUse.session_id == record.id))
    total_uses = await session.scalar(select(func.count(RefreshUse.token_hash)))
    if uses >= 10000 or total_uses >= 100000:
        raise HTTPException(429, "session rotation capacity reached")
    session.add(RefreshUse(token_hash=old, session_id=record.id, next_hash=new,
                           retry_until=now + timedelta(
                               seconds=get_settings().refresh_retry_seconds)))
    record.refresh_hash = new
    await session.commit()
    return response(record)


@router.get("")
async def list_sessions(user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    records = await session.scalars(select(ClientSession).where(ClientSession.user_id == user.id))
    return [{"id": row.id, "created_at": utc(row.created_at),
             "expires_at": utc(row.expires_at), "revoked": row.revoked} for row in records]


@router.delete("/{session_id}")
async def revoke(session_id: str, user: User = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    record = await session.get(ClientSession, session_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(404, "session not found")
    record.revoked = True
    await session.commit()
    return {"revoked": True}
