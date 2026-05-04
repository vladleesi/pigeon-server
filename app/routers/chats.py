"""Send/receive ciphertext messages and read receipts."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..deps import get_current_user
from ..models import (
    ChatMember,
    ChatType,
    PendingMessage,
    ReadReceipt,
    User,
)
from ..schemas import (
    AckRequest,
    ChatInfo,
    IncomingMessage,
    IncomingReadReceipt,
    MarkReadRequest,
    PollResponse,
    SendMessageRequest,
    SendMessageResponse,
    _decode_b64,
)
from ..services import chat_members, ensure_chat_member, load_chat_info
from ..ws_manager import manager as ws_manager

router = APIRouter(prefix="/api/v1", tags=["chats"])

_settings = get_settings()


@router.get("/chats/{chat_id}", response_model=ChatInfo)
async def get_chat(
    chat_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChatInfo:
    chat = await ensure_chat_member(session, chat_id, user)
    return await load_chat_info(session, chat)


@router.post("/chats/{chat_id}/messages", response_model=SendMessageResponse)
async def send_message(
    chat_id: int,
    payload: SendMessageRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SendMessageResponse:
    chat = await ensure_chat_member(session, chat_id, user)
    members = await chat_members(session, chat_id)
    by_pid = {m.public_id: m for m in members}

    if chat.chat_type is ChatType.personal and len(members) < 2:
        raise HTTPException(
            status_code=409,
            detail="personal chat has no peer yet",
        )

    seen_recipients: set[str] = set()
    recipients_users: list[User] = []
    ciphertexts: dict[int, bytes] = {}
    for env in payload.envelopes:
        if env.recipient_public_id == user.public_id:
            raise HTTPException(
                status_code=400, detail="cannot send envelope to self"
            )
        recipient = by_pid.get(env.recipient_public_id)
        if recipient is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown recipient {env.recipient_public_id}",
            )
        if env.recipient_public_id in seen_recipients:
            raise HTTPException(
                status_code=400,
                detail=f"duplicate envelope for {env.recipient_public_id}",
            )
        data = _decode_b64(env.ciphertext)
        if len(data) > _settings.max_ciphertext_bytes:
            raise HTTPException(status_code=413, detail="ciphertext too large")
        seen_recipients.add(env.recipient_public_id)
        recipients_users.append(recipient)
        ciphertexts[recipient.id] = data

    # Every chat member except the sender must receive an envelope.
    required = {m.public_id for m in members if m.id != user.id}
    if seen_recipients != required:
        missing = required - seen_recipients
        extras = seen_recipients - required
        raise HTTPException(
            status_code=400,
            detail={
                "error": "envelopes must cover exactly all peers",
                "missing": sorted(missing),
                "unexpected": sorted(extras),
            },
        )

    now = datetime.now(timezone.utc)
    new_rows = [
        PendingMessage(
            client_message_id=payload.client_message_id,
            chat_id=chat.id,
            sender_id=user.id,
            recipient_id=rec.id,
            ciphertext=ciphertexts[rec.id],
            created_at=now,
        )
        for rec in recipients_users
    ]
    session.add_all(new_rows)
    await session.commit()
    for row in new_rows:
        await session.refresh(row)

    # Push immediately to online recipients.
    for row in new_rows:
        if ws_manager.is_online(row.recipient_id):
            await ws_manager.send_json(
                row.recipient_id,
                {
                    "type": "message",
                    "message": IncomingMessage(
                        id=row.id,
                        client_message_id=row.client_message_id,
                        chat_id=row.chat_id,
                        sender_public_id=user.public_id,
                        ciphertext=base64.b64encode(row.ciphertext).decode("ascii"),
                        created_at=row.created_at,
                    ).model_dump(mode="json"),
                },
            )

    return SendMessageResponse(
        client_message_id=payload.client_message_id,
        recipients=[r.public_id for r in recipients_users],
        created_at=now,
    )


async def _fetch_poll(session: AsyncSession, user: User) -> PollResponse:
    # Pending ciphertext addressed to this user.
    result = await session.execute(
        select(PendingMessage, User)
        .join(User, User.id == PendingMessage.sender_id)
        .where(PendingMessage.recipient_id == user.id)
        .order_by(PendingMessage.id.asc())
    )
    pending_rows = result.all()

    to_mark = []
    now = datetime.now(timezone.utc)
    incoming: list[IncomingMessage] = []
    for msg, sender in pending_rows:
        incoming.append(
            IncomingMessage(
                id=msg.id,
                client_message_id=msg.client_message_id,
                chat_id=msg.chat_id,
                sender_public_id=sender.public_id,
                ciphertext=base64.b64encode(msg.ciphertext).decode("ascii"),
                created_at=msg.created_at,
            )
        )
        if msg.delivered_at is None:
            msg.delivered_at = now
            to_mark.append(msg)

    # Read receipts for messages this user originally sent.
    receipts_result = await session.execute(
        select(ReadReceipt).where(ReadReceipt.sender_id == user.id).order_by(ReadReceipt.id.asc())
    )
    receipts = list(receipts_result.scalars().all())
    incoming_receipts = [
        IncomingReadReceipt(
            id=r.id,
            client_message_id=r.client_message_id,
            chat_id=r.chat_id,
            reader_public_id=r.reader_public_id,
            created_at=r.created_at,
        )
        for r in receipts
    ]

    if to_mark:
        await session.commit()

    return PollResponse(messages=incoming, read_receipts=incoming_receipts)


@router.get("/poll", response_model=PollResponse)
async def poll(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    _: int = Query(default=0, description="optional cache-buster"),
) -> PollResponse:
    return await _fetch_poll(session, user)


@router.post("/ack")
async def ack(
    payload: AckRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Confirm local persistence so rows can be deleted on the server."""

    deleted_messages = 0
    deleted_receipts = 0
    if payload.message_ids:
        res = await session.execute(
            delete(PendingMessage).where(
                and_(
                    PendingMessage.recipient_id == user.id,
                    PendingMessage.id.in_(payload.message_ids),
                )
            )
        )
        deleted_messages = res.rowcount or 0
    if payload.read_ids:
        res = await session.execute(
            delete(ReadReceipt).where(
                and_(
                    ReadReceipt.sender_id == user.id,
                    ReadReceipt.id.in_(payload.read_ids),
                )
            )
        )
        deleted_receipts = res.rowcount or 0
    await session.commit()
    return {"deleted_messages": deleted_messages, "deleted_receipts": deleted_receipts}


@router.post("/chats/{chat_id}/read")
async def mark_read(
    chat_id: int,
    payload: MarkReadRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Mark inbound ciphertext as read.

    Rows are deleted on the server while senders receive receipts via poll/ws;
    receipts themselves are removed after the sender ACKs them.
    """

    chat = await ensure_chat_member(session, chat_id, user)

    # Match pending rows for this chat/recipient and the given client_message_ids.
    result = await session.execute(
        select(PendingMessage)
        .where(
            PendingMessage.chat_id == chat.id,
            PendingMessage.recipient_id == user.id,
            PendingMessage.client_message_id.in_(payload.client_message_ids),
        )
    )
    msgs = list(result.scalars().all())
    if not msgs:
        return {"marked": 0}

    now = datetime.now(timezone.utc)
    receipts: list[ReadReceipt] = []
    ids_to_delete: list[int] = []
    for msg in msgs:
        receipts.append(
            ReadReceipt(
                client_message_id=msg.client_message_id,
                chat_id=msg.chat_id,
                sender_id=msg.sender_id,
                reader_public_id=user.public_id,
                kind="read",
                created_at=now,
            )
        )
        ids_to_delete.append(msg.id)

    session.add_all(receipts)
    if ids_to_delete:
        await session.execute(
            delete(PendingMessage).where(PendingMessage.id.in_(ids_to_delete))
        )
    await session.commit()
    for r in receipts:
        await session.refresh(r)

    # Push receipts to online senders.
    for r in receipts:
        if ws_manager.is_online(r.sender_id):
            await ws_manager.send_json(
                r.sender_id,
                {
                    "type": "read",
                    "read": IncomingReadReceipt(
                        id=r.id,
                        client_message_id=r.client_message_id,
                        chat_id=r.chat_id,
                        reader_public_id=r.reader_public_id,
                        created_at=r.created_at,
                    ).model_dump(mode="json"),
                },
            )

    return {"marked": len(msgs)}


@router.delete("/chats/{chat_id}/outbox")
async def drop_undelivered(
    chat_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Drop this user's own unread ciphertext still queued on the server."""

    await ensure_chat_member(session, chat_id, user)
    res = await session.execute(
        delete(PendingMessage).where(
            PendingMessage.chat_id == chat_id,
            PendingMessage.sender_id == user.id,
        )
    )
    await session.commit()
    return {"deleted": res.rowcount or 0}
