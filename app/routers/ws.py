"""WebSocket endpoint for realtime ciphertext delivery and receipts."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import PendingMessage, ReadReceipt, User
from ..schemas import IncomingMessage, IncomingReadReceipt
from ..security import decode_client_token
from ..ws_manager import manager

router = APIRouter()


async def _resolve_user(session: AsyncSession, token: str) -> User | None:
    try:
        payload = decode_client_token(token)
    except jwt.InvalidTokenError:
        return None
    if payload.get("typ") != "client":
        return None
    try:
        user_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        return None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def _backlog_payload(
    session: AsyncSession, user: User
) -> tuple[list[IncomingMessage], list[IncomingReadReceipt]]:
    msg_res = await session.execute(
        select(PendingMessage, User)
        .join(User, User.id == PendingMessage.sender_id)
        .where(PendingMessage.recipient_id == user.id)
        .order_by(PendingMessage.id.asc())
    )
    messages = [
        IncomingMessage(
            id=msg.id,
            client_message_id=msg.client_message_id,
            chat_id=msg.chat_id,
            sender_public_id=sender.public_id,
            ciphertext=base64.b64encode(msg.ciphertext).decode("ascii"),
            created_at=msg.created_at,
        )
        for msg, sender in msg_res.all()
    ]

    r_res = await session.execute(
        select(ReadReceipt)
        .where(ReadReceipt.sender_id == user.id)
        .order_by(ReadReceipt.id.asc())
    )
    receipts = [
        IncomingReadReceipt(
            id=r.id,
            client_message_id=r.client_message_id,
            chat_id=r.chat_id,
            reader_public_id=r.reader_public_id,
            created_at=r.created_at,
        )
        for r in r_res.scalars().all()
    ]
    return messages, receipts


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query(...)) -> None:
    async with SessionLocal() as session:
        user = await _resolve_user(session, token)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user.last_seen_at = datetime.now(timezone.utc)
        await session.commit()

        await websocket.accept()
        await manager.connect(user.id, websocket)

        messages, receipts = await _backlog_payload(session, user)

        # Backlog rows are treated as delivered once streamed down.
        if messages:
            ids = [m.id for m in messages]
            pending_rows = await session.execute(
                select(PendingMessage).where(PendingMessage.id.in_(ids))
            )
            now = datetime.now(timezone.utc)
            for row in pending_rows.scalars().all():
                if row.delivered_at is None:
                    row.delivered_at = now
            await session.commit()

        try:
            await websocket.send_json(
                {
                    "type": "hello",
                    "user": user.public_id,
                    "backlog": {
                        "messages": [m.model_dump(mode="json") for m in messages],
                        "read_receipts": [r.model_dump(mode="json") for r in receipts],
                    },
                }
            )
        except WebSocketDisconnect:
            await manager.disconnect(user.id, websocket)
            return

    try:
        while True:
            # Clients may send ping frames; accept simple textual pings too.
            raw = await websocket.receive_text()
            if raw == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user.id, websocket)
