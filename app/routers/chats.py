"""Send/receive ciphertext messages and read receipts."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..deps import get_current_user
from ..models import (
    ChatType,
    PendingMessage,
    ReadReceipt,
    SendRecord,
    User,
)
from ..schemas import (
    AckRequest,
    ChatInfo,
    ExactAckRequest,
    ExactMarkReadRequest,
    IncomingMessage,
    IncomingReadReceipt,
    MarkReadRequest,
    MessageReference,
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
    await session.execute(text("BEGIN IMMEDIATE"))
    chat = await ensure_chat_member(session, chat_id, user)
    now = datetime.now(timezone.utc)
    digest = hashlib.sha256(json.dumps(sorted(
        (env.recipient_public_id, hashlib.sha256(_decode_b64(env.ciphertext)).hexdigest())
        for env in payload.envelopes
    ), separators=(",", ":")).encode()).hexdigest()
    record = await session.scalar(select(SendRecord).where(
        SendRecord.chat_id == chat_id, SendRecord.sender_id == user.id,
        SendRecord.client_message_id == payload.client_message_id,
    ))
    if record and record.expires_at.replace(tzinfo=timezone.utc) > now:
        if record.payload_hash != digest:
            raise HTTPException(409, "message identity already has a different payload")
        return SendMessageResponse(client_message_id=payload.client_message_id,
                                   recipients=json.loads(record.recipients_json),
                                   created_at=record.created_at)
    if record:
        await session.delete(record)
        await session.flush()
    else:
        legacy = await session.scalar(select(PendingMessage.id).where(
            PendingMessage.chat_id == chat_id, PendingMessage.sender_id == user.id,
            PendingMessage.client_message_id == payload.client_message_id,
        ).limit(1))
        if legacy is not None:
            raise HTTPException(409, "message predates retry ledger; verify delivery")
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

    count, size = (await session.execute(select(
        func.count(PendingMessage.id),
        func.coalesce(func.sum(func.length(PendingMessage.ciphertext)), 0)
    ))).one()
    ledger_count = await session.scalar(select(func.count(SendRecord.id)))
    own_count, own_bytes = (await session.execute(select(
        func.count(PendingMessage.id),
        func.coalesce(func.sum(func.length(PendingMessage.ciphertext)), 0),
    ).where(PendingMessage.sender_id == user.id))).one()
    own_receipts = await session.scalar(select(func.count(ReadReceipt.id)).where(
        ReadReceipt.sender_id == user.id))
    recent = await session.scalar(select(func.count(SendRecord.id)).where(
        SendRecord.sender_id == user.id, SendRecord.created_at > now - timedelta(minutes=1)))
    if (count + len(recipients_users) > _settings.max_pending_messages
            or size + sum(map(len, ciphertexts.values())) > _settings.max_pending_bytes
            or ledger_count >= _settings.max_send_records
            or own_count + own_receipts + len(recipients_users) > _settings.max_pending_per_sender
            or (own_bytes + sum(map(len, ciphertexts.values()))
                > _settings.max_pending_bytes_per_sender)
            or recent >= _settings.sends_per_minute):
        raise HTTPException(429, "relay capacity reached; retry later",
                            headers={"Retry-After": "60"})
    session.add(SendRecord(
        chat_id=chat_id, sender_id=user.id, client_message_id=payload.client_message_id,
        payload_hash=digest, recipients_json=json.dumps([r.public_id for r in recipients_users]),
        created_at=now, expires_at=now + timedelta(days=_settings.send_idempotency_days),
    ))
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
    await session.flush()
    await session.commit()

    # Push immediately to online recipients.
    for row in new_rows:
        if ws_manager.is_online(row.recipient_id):
            await ws_manager.send_json(
                row.recipient_id,
                {
                    "type": "message",
                    "message": IncomingMessage(
                        id=row.id,
                        delivery_id=row.delivery_id,
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
        .order_by(PendingMessage.id.asc()).limit(100)
    )
    pending_rows = result.all()

    to_mark = []
    now = datetime.now(timezone.utc)
    incoming: list[IncomingMessage] = []
    for msg, sender in pending_rows:
        incoming.append(
            IncomingMessage(
                id=msg.id,
                delivery_id=msg.delivery_id,
                client_message_id=msg.client_message_id,
                chat_id=msg.chat_id,
                sender_public_id=sender.public_id,
                ciphertext=base64.b64encode(msg.ciphertext).decode("ascii"),
                created_at=msg.created_at,
            )
        )
        if msg.delivered_at is None:
            to_mark.append(msg.delivery_id)

    # Read receipts for messages this user originally sent.
    receipts_result = await session.execute(
        select(ReadReceipt).where(ReadReceipt.sender_id == user.id)
        .order_by(ReadReceipt.id.asc()).limit(100)
    )
    receipts = list(receipts_result.scalars().all())
    incoming_receipts = [
        IncomingReadReceipt(
            id=r.id,
            delivery_id=r.delivery_id,
            client_message_id=r.client_message_id,
            chat_id=r.chat_id,
            reader_public_id=r.reader_public_id,
            created_at=r.created_at,
        )
        for r in receipts
    ]

    if to_mark:
        await session.execute(update(PendingMessage).where(
            PendingMessage.delivery_id.in_(to_mark),
            PendingMessage.recipient_id == user.id,
        ).values(delivered_at=now))
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

    if not _settings.allow_legacy_ack:
        raise HTTPException(410, "use /ack/exact")
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

    if not _settings.allow_legacy_ack:
        raise HTTPException(410, "use /read/exact")
    chat = await ensure_chat_member(session, chat_id, user)

    # Match pending rows for this chat/recipient and the given client_message_ids.
    result = await session.execute(
        delete(PendingMessage)
        .where(
            PendingMessage.chat_id == chat.id,
            PendingMessage.recipient_id == user.id,
            PendingMessage.client_message_id.in_(payload.client_message_ids),
        )
        .returning(PendingMessage)
    )
    msgs = list(result.scalars().all())
    return await _finish_read(session, user, msgs)


def _message_match(ref: MessageReference):
    return and_(
        PendingMessage.delivery_id == ref.delivery_id,
        PendingMessage.chat_id == ref.chat_id,
        PendingMessage.client_message_id == ref.client_message_id,
        PendingMessage.sender_id.in_(select(User.id).where(User.public_id == ref.sender_public_id)),
    )


@router.post("/ack/exact")
async def ack_exact(
    payload: ExactAckRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Delete only the identified deliveries owned by the authenticated user."""
    messages = receipts = 0
    if payload.messages:
        result = await session.execute(delete(PendingMessage).where(
            PendingMessage.recipient_id == user.id,
            or_(*(_message_match(ref) for ref in payload.messages)),
        ))
        messages = result.rowcount or 0
    if payload.receipts:
        result = await session.execute(delete(ReadReceipt).where(
            ReadReceipt.sender_id == user.id,
            or_(*(and_(
                ReadReceipt.delivery_id == ref.delivery_id,
                ReadReceipt.chat_id == ref.chat_id,
                ReadReceipt.client_message_id == ref.client_message_id,
                ReadReceipt.reader_public_id == ref.reader_public_id,
            ) for ref in payload.receipts)),
        ))
        receipts = result.rowcount or 0
    await session.commit()
    return {"deleted_messages": messages, "deleted_receipts": receipts}


@router.post("/chats/{chat_id}/read/exact")
async def mark_read_exact(
    chat_id: int,
    payload: ExactMarkReadRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    await ensure_chat_member(session, chat_id, user)
    if any(ref.chat_id != chat_id for ref in payload.messages):
        raise HTTPException(status_code=422, detail="message chat does not match route")
    # DELETE RETURNING makes row consumption atomic across concurrent tabs and
    # workers. Only the winner creates a receipt, in the same transaction.
    result = await session.execute(delete(PendingMessage).where(
        PendingMessage.recipient_id == user.id,
        or_(*(_message_match(ref) for ref in payload.messages)),
    ).returning(PendingMessage))
    return await _finish_read(session, user, list(result.scalars().all()))


async def _finish_read(
    session: AsyncSession, user: User, msgs: list[PendingMessage],
) -> dict[str, int]:
    if not msgs:
        await session.commit()
        return {"marked": 0}

    receipt_count = await session.scalar(select(func.count(ReadReceipt.id)))
    if receipt_count + len(msgs) > _settings.max_read_receipts:
        await session.rollback()
        raise HTTPException(429, "receipt capacity reached", headers={"Retry-After": "60"})
    now = datetime.now(timezone.utc)
    receipts: list[ReadReceipt] = []
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

    session.add_all(receipts)
    await session.flush()
    await session.commit()

    # Push receipts to online senders.
    for r in receipts:
        if ws_manager.is_online(r.sender_id):
            await ws_manager.send_json(
                r.sender_id,
                {
                    "type": "read",
                    "read": IncomingReadReceipt(
                        id=r.id,
                        delivery_id=r.delivery_id,
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
