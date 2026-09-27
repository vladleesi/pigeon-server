"""WebSocket authentication transport tests."""

import asyncio
import secrets
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

from app.db import SessionLocal, init_db
from app.main import create_app
from app.models import Link, LinkType, User
from app.routers.ws import _token_from_subprotocol
from app.security import create_client_token


async def _receive() -> dict:
    return {"type": "websocket.disconnect"}


async def _send(message: dict) -> None:
    del message


def test_token_can_be_read_from_websocket_subprotocol() -> None:
    websocket = WebSocket(
        {
            "type": "websocket",
            "path": "/ws",
            "headers": [
                (
                    b"sec-websocket-protocol",
                    b"sideword.v1, sideword.auth.header.payload.signature",
                )
            ],
            "query_string": b"",
            "scheme": "ws",
            "server": ("test", 80),
            "client": ("test", 123),
            "subprotocols": [],
        },
        _receive,
        _send,
    )

    assert _token_from_subprotocol(websocket) == "header.payload.signature"


async def _create_websocket_user() -> tuple[User, Link]:
    await init_db()
    async with SessionLocal() as session:
        user = User(
            public_id=secrets.token_hex(6),
            display_name="WebSocket test",
            public_key=secrets.token_bytes(32),
        )
        link = Link(
            token=secrets.token_urlsafe(32),
            link_type=LinkType.group,
            max_uses=0,
            uses_count=0,
            is_active=True,
        )
        session.add_all([user, link])
        await session.commit()
        await session.refresh(user)
        await session.refresh(link)
        return user, link


def test_websocket_accepts_private_auth_subprotocol() -> None:
    user, link = asyncio.run(_create_websocket_user())
    token = create_client_token(user.id, user.public_id, link.id)

    with TestClient(create_app()) as client:
        with client.websocket_connect(
            "/ws",
            subprotocols=["sideword.v1", f"sideword.auth.{token}"],
        ) as websocket:
            hello = websocket.receive_json()

            assert websocket.accepted_subprotocol == "sideword.v1"
            assert hello["type"] == "hello"
            assert hello["user"] == user.public_id


def test_websocket_accepts_authentication_first_frame() -> None:
    user, link = asyncio.run(_create_websocket_user())
    token = create_client_token(user.id, user.public_id, link.id)

    with TestClient(create_app()) as client:
        with client.websocket_connect(
            "/ws",
            subprotocols=["sideword.v1"],
        ) as websocket:
            websocket.send_json({"type": "auth", "token": token})
            hello = websocket.receive_json()

            assert websocket.accepted_subprotocol == "sideword.v1"
            assert hello["type"] == "hello"
            assert hello["user"] == user.public_id


def test_websocket_rejects_invalid_first_frame_session() -> None:
    with TestClient(create_app()) as client:
        with client.websocket_connect(
            "/ws",
            subprotocols=["sideword.v1"],
        ) as websocket:
            websocket.send_json({"type": "auth", "token": "not-a-valid-token"})

            assert websocket.receive_json() == {
                "type": "auth_error",
                "reason": "invalid session",
            }


def test_websocket_rejects_session_from_revoked_invite() -> None:
    user, link = asyncio.run(_create_websocket_user())
    token = create_client_token(user.id, user.public_id, link.id)

    async def revoke() -> None:
        async with SessionLocal() as session:
            stored = await session.get(Link, link.id)
            assert stored is not None
            stored.is_active = False
            stored.revoked_at = datetime.now(timezone.utc)
            await session.commit()

    asyncio.run(revoke())

    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws", subprotocols=["sideword.v1"]) as websocket:
            websocket.send_json({"type": "auth", "token": token})
            assert websocket.receive_json() == {
                "type": "auth_error",
                "reason": "invalid session",
            }
