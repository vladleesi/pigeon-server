"""Admission security, retry safety, and SQLite concurrency regression tests."""

import asyncio
import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import db
from app.db import SessionLocal
from app.invite_security import hash_room_password, verify_room_password
from app.main import create_app
from app.models import ChatMember, Link, User

PASSWORD = "a room phrase for testing only"


@pytest.fixture
def client():
    with TestClient(create_app(), base_url="https://testserver") as client:
        assert client.post("/admin/login", data={
            "username": "admin", "password": "adminpass",
        }, follow_redirects=False).status_code == 303
        yield client


def create_invite(client, **fields):
    response = client.post("/admin/links", data={
        "link_type": "personal", "password_mode": "custom", "room_password": PASSWORD,
        **fields,
    }, follow_redirects=False)
    assert response.status_code in (200, 303)
    links = client.get("/admin/api/export").json()["links"]
    return links[-1], response


def join_payload(**fields):
    return {
        "public_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "password": PASSWORD,
        "resume_credential": secrets.token_urlsafe(32),
        **fields,
    }


def join(client, link, payload, **kwargs):
    return client.post(f"/api/v1/links/{link['token']}/activate", json=payload, **kwargs)


def state(link):
    async def read():
        async with SessionLocal() as session:
            item = await session.scalar(select(Link).where(Link.token == link["token"]))
            members = await session.scalar(select(func.count(ChatMember.id)).where(
                ChatMember.chat_id == item.chat_id,
            ))
            return item.uses_count, members, item.is_active, item.failed_attempts
    return asyncio.run(read())


def change_link(link, **fields):
    async def change():
        async with SessionLocal() as session:
            item = await session.scalar(select(Link).where(Link.token == link["token"]))
            for name, value in fields.items():
                setattr(item, name, value)
            await session.commit()
    asyncio.run(change())


def test_password_hash_is_salted_and_never_truncates():
    first = hash_room_password(PASSWORD)
    assert first != hash_room_password(PASSWORD)
    assert PASSWORD not in first
    assert verify_room_password(PASSWORD, first)
    assert not verify_room_password(PASSWORD + "!", first)
    long_password = "x" * 32
    verifier = hash_room_password(long_password)
    assert verify_room_password(long_password, verifier)
    assert not verify_room_password("x" * 31 + "y", verifier)


def test_short_custom_password_is_shown_once_and_requires_eight_characters(client):
    link, response = create_invite(client, room_password="eight123")
    assert response.status_code == 200
    assert 'value="eight123"' in response.text
    assert "8–32 characters." in response.text
    assert 'maxlength="32" aria-describedby="room-password-help' in response.text
    assert join(client, link, join_payload(password="eight123")).status_code == 200
    assert "eight123" not in client.get("/admin/links").text
    rejected = client.post("/admin/links", data={
        "link_type": "personal", "password_mode": "custom", "room_password": "seven12",
    })
    assert rejected.status_code == 422
    assert "8 to 32" in rejected.text


def test_views_and_failed_auth_do_not_claim_slots(client):
    link, _ = create_invite(client)
    assert link["password_required"]
    assert link["password_hash"].startswith("scrypt-v1$")
    for _ in range(3):
        for path in (f"/l/{link['token']}", f"/client?invite={link['token']}"):
            response = client.get(path, headers={"User-Agent": "LinkPreviewBot"})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert PASSWORD not in response.text
    assert join(client, link, join_payload(password=None)).status_code == 403
    assert join(client, link, join_payload(password="incorrect phrase")).status_code == 403
    assert state(link) == (0, 0, True, 2)
    assert join(client, {"token": "unknown-invite"}, join_payload()).status_code == 404
    assert join(client, link, join_payload()).status_code == 200
    assert state(link)[:2] == (1, 1)


def test_authenticated_retries_seal_and_reconnect(client):
    link, _ = create_invite(client)
    alice = join_payload()
    first = join(client, link, alice).json()
    # Lost response retry, before the client has received any JWT.
    resumed = join(client, link, {**alice, "password": None})
    assert resumed.status_code == 200
    assert resumed.json()["user"]["public_id"] == first["user"]["public_id"]
    assert state(link)[:2] == (1, 1)
    # Knowing a public key and phrase cannot impersonate an existing participant.
    assert join(client, link, {**alice, "resume_credential": secrets.token_urlsafe(32)}
                ).status_code == 409
    assert join(client, link, join_payload()).status_code == 200
    assert state(link)[:3] == (2, 2, False)
    assert "room is sealed" in client.get(f"/l/{link['token']}").text
    assert join(client, link, join_payload()).status_code == 410
    assert join(client, link, {**alice, "password": None}).status_code == 200
    # A signed existing session works even without the optional retry credential.
    assert join(client, link, {**alice, "password": None, "resume_credential": None},
                headers={"Authorization": f"Bearer {first['token']}"}).status_code == 200
    assert state(link)[:3] == (2, 2, False)


def test_rate_limit_survives_new_application_and_does_not_block_members(client):
    link, _ = create_invite(client)
    alice = join_payload()
    assert join(client, link, alice).status_code == 200
    for _ in range(5):
        assert join(client, link, join_payload(password="wrong")).status_code == 403
    blocked = join(client, link, join_payload())
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 300
    with TestClient(create_app(), base_url="https://testserver") as second:
        assert join(second, link, join_payload(), headers={
            "X-Forwarded-For": "192.0.2.2",
        }).status_code == 429
        assert join(second, link, {**alice, "password": None}).status_code == 200
    change_link(link, failed_window_started_at=datetime.now(timezone.utc) - timedelta(minutes=6))
    assert join(client, link, join_payload()).status_code == 200


@pytest.mark.parametrize("field", ["expires_at", "revoked_at", "is_deleted"])
def test_lifecycle_invalidates_resumption_and_http_sessions(client, field):
    link, _ = create_invite(client)
    alice = join_payload()
    token = join(client, link, alice).json()["token"]
    change_link(link, **{field: True if field == "is_deleted"
                       else datetime.now(timezone.utc) - timedelta(seconds=1)})
    assert join(client, link, alice).status_code in (404, 410)
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}
                      ).status_code == 401


def test_concurrent_first_joins_and_retries_are_atomic(client):
    link, _ = create_invite(client)
    alice = join_payload()

    async def race(payloads):
        async with AsyncClient(transport=ASGITransport(app=create_app()),
                               base_url="https://testserver") as c:
            return await asyncio.gather(*[
                c.post(f"/api/v1/links/{link['token']}/activate", json=payload)
                for payload in payloads
            ])

    responses = asyncio.run(race([alice] * 4))
    assert [r.status_code for r in responses] == [200] * 4
    assert len({r.json()["user"]["public_id"] for r in responses}) == 1
    assert state(link)[:2] == (1, 1)
    responses = asyncio.run(race([join_payload() for _ in range(5)]))
    assert sorted(r.status_code for r in responses) == [200, 410, 410, 410, 410]
    assert state(link)[:3] == (2, 2, False)


def test_generated_password_is_once_only_and_custom_errors_do_not_echo(client):
    link, response = create_invite(client, password_mode="generated", room_password="")
    match = re.search(r'Room password</span> <input[^>]+value="([^"]+)"', response.text)
    assert match is not None
    generated = match.group(1)
    assert response.headers["cache-control"] == "no-store"
    assert len(generated) == 16
    assert generated not in client.get("/admin/links").text
    assert generated not in json.dumps(link)
    assert join(client, link, join_payload(password=generated)).status_code == 200
    for password in ("", "short", " " * 20, "x" * 33):
        invalid = client.post("/admin/links", data={
            "link_type": "personal", "password_mode": "custom", "room_password": password,
        })
        assert invalid.status_code == 422
        assert 'id="room-password-error" role="alert" >' in invalid.text
        if password.strip():
            assert password not in invalid.text


def test_generated_invite_list_get_shows_current_admissions_without_creating_links(client):
    link, response = create_invite(client, password_mode="generated", room_password="")
    password = re.search(r'Room password</span> <input[^>]+value="([^"]+)"', response.text).group(1)
    assert 'id="created-invite-panel"' in response.text
    for _ in range(2):
        assert join(client, link, join_payload(password=password)).status_code == 200
    count = len(client.get("/admin/api/export").json()["links"])
    for _ in range(2):
        refreshed = client.get("/admin/links")
        assert refreshed.status_code == 200
        assert refreshed.headers["cache-control"] == "no-store"
        assert password not in refreshed.text
        assert 'id="created-invite-panel"' in refreshed.text
        assert 'class="table-scroll"' not in refreshed.text
        # Find the row by its URL without printing its private contents on failure.
        rows = re.findall(r'<tr data-link-id="\d+">(.*?)</tr>', refreshed.text, re.S)
        row = next(row for row in rows if link["token"] in row)
        assert re.search(r'data-live-field="uses">\s*2 / 2\s*</td>', row)
        assert 'fully used' in row
        assert len(client.get("/admin/api/export").json()["links"]) == count


def test_group_limit_and_unprotected_backwards_compatibility(client):
    link, _ = create_invite(client, link_type="group", participant_limit="3")
    for _ in range(3):
        assert join(client, link, join_payload()).status_code == 200
    assert join(client, link, join_payload()).status_code == 410
    assert state(link)[:3] == (3, 3, False)
    link, _ = create_invite(client, password_mode="none", room_password="ignored")
    payload = join_payload()
    del payload["password"]
    del payload["resume_credential"]
    assert join(client, link, payload).status_code == 200


def test_https_and_validation_response_privacy(client):
    link, _ = create_invite(client)
    with TestClient(create_app(), base_url="http://testserver") as insecure:
        assert join(insecure, link, join_payload(), headers={
            "X-Forwarded-Proto": "https",
        }).status_code == 400
    response = join(client, link, join_payload(password={"secret": PASSWORD}))
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert state(link)[:2] == (0, 0)


def test_export_import_preserves_protection_and_resume(client):
    link, _ = create_invite(client)
    alice = join_payload()
    first = join(client, link, alice).json()
    bundle = client.get("/admin/api/export").json()
    assert PASSWORD not in json.dumps(bundle)
    assert alice["resume_credential"] not in json.dumps(bundle)
    # Import only this room with a fresh token, avoiding destructive replace.
    exported = next(item for item in bundle["links"] if item["token"] == link["token"])
    chat = bundle["chats"][exported["chat_index"]]
    exported["chat_index"] = 0
    exported["token"] = secrets.token_urlsafe(32)
    small = {"version": 1, "users": [u for u in bundle["users"]
             if u["public_id"] == first["user"]["public_id"]],
             "chats": [chat], "links": [exported]}
    response = client.post("/admin/api/import", files={
        "bundle": ("test.json", json.dumps(small), "application/json"),
    })
    assert response.status_code == 200
    assert join(client, exported, join_payload(password="wrong")).status_code == 403
    resumed = join(client, exported, {**alice, "password": None})
    assert resumed.status_code == 200
    assert resumed.json()["user"]["public_id"] == first["user"]["public_id"]
    exported["password_hash"] = None
    assert client.post("/admin/api/import", files={
        "bundle": ("test.json", json.dumps(small), "application/json"),
    }).status_code == 400


def test_legacy_migration_is_additive_and_repeatable(tmp_path, monkeypatch):
    async def check():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.sqlite3'}")
        monkeypatch.setattr(db, "engine", engine)
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE links (id INTEGER PRIMARY KEY, is_active BOOLEAN)"
            ))
            await conn.execute(text("CREATE TABLE chat_members (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO links VALUES (1, 1)"))
        await db.init_db()
        await db.init_db()
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT password_hash, failed_attempts, revoked_at FROM links WHERE id = 1"
            ))).one()
            assert tuple(row) == (None, 0, None)
            columns = await conn.execute(text("PRAGMA table_info(chat_members)"))
            assert "resume_hash" in {row[1] for row in columns}
        await engine.dispose()
    asyncio.run(check())


def test_deactivated_participant_cannot_resume(client):
    link, _ = create_invite(client)
    alice = join_payload()
    user = join(client, link, alice).json()["user"]

    async def deactivate():
        async with SessionLocal() as session:
            item = await session.scalar(select(User).where(User.public_id == user["public_id"]))
            item.is_active = False
            await session.commit()
    asyncio.run(deactivate())
    assert join(client, link, alice).status_code == 401


def test_existing_jwt_cannot_bypass_another_rooms_password(client):
    first_link, _ = create_invite(client, password_mode="none")
    alice = join_payload(password=None)
    first = join(client, first_link, alice).json()
    protected_link, _ = create_invite(client)
    auth = {"Authorization": f"Bearer {first['token']}"}
    assert join(client, protected_link, alice, headers=auth).status_code == 403
    assert state(protected_link)[:2] == (0, 0)
    assert join(client, protected_link, {**alice, "password": PASSWORD},
                headers=auth).status_code == 200


def test_legacy_authenticated_member_can_add_retry_credential(client):
    link, _ = create_invite(client, password_mode="none")
    alice = join_payload(password=None)
    first = join(client, link, {**alice, "resume_credential": None}).json()
    assert join(client, link, alice, headers={
        "Authorization": f"Bearer {first['token']}",
    }).status_code == 200
    assert join(client, link, alice).status_code == 200
    assert state(link)[:2] == (1, 1)
