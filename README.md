# Pigeon Server

Backend for a link-only messenger. The server:

- Creates and revokes **personal** (1:1) and **group** invite links.
- Accepts clients who activate those links.
- Relays **client-encrypted** messages end-to-end — the server only sees ciphertext and metadata.
- Does **not** keep a permanent archive: delivered and read messages are removed.
- Provides a minimal web admin UI for monitoring and exporting/importing configuration to another host.

---

## Quick start

### 1. Prerequisites

Docker 24+ and Docker Compose v2.

```bash
git clone <repo> pigeon-server
cd pigeon-server
cp .env.example .env
# Edit .env and set:
#   PIGEON_SECRET_KEY       — long random secret (JWT, sessions)
#   PIGEON_ADMIN_USERNAME   — first admin username
#   PIGEON_ADMIN_PASSWORD   — first admin password (you can remove from .env later)
#   PIGEON_PUBLIC_URL       — public URL (e.g. https://pigeon.example.com)
```

Generate a secret quickly:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 2. Run

```bash
docker compose up -d --build
```

The API listens on `http://localhost:8000`.

- `http://localhost:8000/health` — liveness.
- `http://localhost:8000/admin/login` — admin UI login.
- `http://localhost:8000/l/<token>` — informational landing page for a link (activation is in the app).
- `http://localhost:8000/docs` — OpenAPI docs.

### 3. Stop

```bash
docker compose stop         # stop without losing data
docker compose start        # start again
docker compose restart      # restart
```

### 4. Remove from the server

```bash
docker compose down -v      # stop, remove containers and volumes
docker image rm pigeon-server:latest 2>/dev/null || true
rm -rf exports .env         # optional: remove exports and local .env
```

The database volume is named `pigeon-data` and is removed with `-v`. After the steps above, no Pigeon data or images remain.

---

## Bare-metal install

1. Install Docker and Docker Compose.
2. Copy the repo (or at least `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `app/`, `scripts/`, `.env.example`).
3. Create `.env` from the example.
4. Run `docker compose up -d --build`.
5. Terminate TLS at a reverse proxy (nginx/Caddy/Traefik) for your public domain and set `PIGEON_PUBLIC_URL`. TLS is required in production — payloads are E2E encrypted, but transport TLS protects WebSocket sessions and cookies.

Minimal nginx fragment:

```nginx
server {
    listen 443 ssl http2;
    server_name pigeon.example.com;
    ssl_certificate     /etc/letsencrypt/live/pigeon.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pigeon.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_read_timeout 3600;
    }
}
```

---

## Migrate to another server

Export configuration (users, chats, links — **no** message history):

- **Admin UI**: Export → Download JSON.
- **CLI inside the container**:

  ```bash
  docker compose exec pigeon python -m scripts.export_config \
      --output /exports/pigeon-config.json
  # file appears on the host under ./exports/
  ```

On the new server, after `docker compose up -d`, upload the file via Export → Upload. Enable **full replace** if you want to wipe existing data there.

> Message history is intentionally not exported: the server does not store it; clients keep it locally.

---

## Admin UI

| Path | Purpose |
| --- | --- |
| `/admin/login` | Login |
| `/admin/` | Dashboard |
| `/admin/links` | Create/revoke personal and group links |
| `/admin/users` | Users, key fingerprints, block/unblock |
| `/admin/chats` | Chats and purge undelivered queue |
| `/admin/export-ui` | Export/import config |

The first admin is created from `PIGEON_ADMIN_USERNAME` and `PIGEON_ADMIN_PASSWORD` on first startup. After login you may remove those lines from `.env` — the record is already in the database.

Create or rotate an admin password:

```bash
docker compose exec pigeon python -m scripts.create_admin --username alice
# password is prompted interactively
```

---

## Mobile client API

Flow: the client receives `https://<PIGEON_PUBLIC_URL>/l/<token>`, **opens it in the app**, and calls HTTP. Activation is not intended for browsers only.

### Activate link

```
POST /api/v1/links/{token}/activate
Content-Type: application/json

{
  "public_key": "<base64, 32 bytes, X25519>",
  "display_name": "Alice"          // optional
}
```

Response:

```json
{
  "token": "<client JWT>",
  "user": { "public_id": "...", "public_key": "...", "key_fingerprint": "..." },
  "chat": {
    "id": 1,
    "chat_type": "personal|group",
    "participants": [ { "public_id": "...", "public_key": "...", ... } ]
  }
}
```

- Personal links allow **at most two** activations; then the link closes automatically.
- Group links are multi-use; each activation joins the same group.
- If the device already sends `Authorization: Bearer ...`, activation **does not** create a new user — it adds the current user to the new chat/group.

### Profile and chats

```
GET /api/v1/me
Authorization: Bearer <JWT>
```

Returns the current user, chats, and each participant’s public key (used to encrypt outbound envelopes).

### Send message

```
POST /api/v1/chats/{chat_id}/messages
Authorization: Bearer <JWT>

{
  "client_message_id": "uuid or hash",
  "envelopes": [
    { "recipient_public_id": "<peer>", "ciphertext": "<base64>" },
    ...
  ]
}
```

Rules:

- `envelopes` must cover **exactly all** chat members except the sender.
- `ciphertext` is opaque client-side encryption (recommended: NaCl/libsodium `crypto_box` or `crypto_secretbox` with nonce inside the blob).
- Max ciphertext length: `PIGEON_MAX_CIPHERTEXT_BYTES` (default 64 KiB).
- `client_message_id` ties to local history and receipts.

### Receive messages and receipts

1. **WebSocket** (recommended):

   ```
   GET /ws?token=<JWT>
   ```

   The `hello` frame includes backlog (offline messages and receipts); then `message` and `read` events stream live.

2. **Polling fallback**:

   ```
   GET /api/v1/poll
   Authorization: Bearer <JWT>
   ```

   Returns `messages` and `read_receipts` arrays.

### Mark read

```
POST /api/v1/chats/{chat_id}/read
{ "client_message_ids": ["m-1", "m-2"] }
```

The ciphertext row is removed from the server. A read receipt is created for the sender (poll/ws); after ACK it is deleted.

### Acknowledge server-side deletion

```
POST /api/v1/ack
{
  "message_ids": [123, 124],   // fetched and persisted locally
  "read_ids":    [45]          // read receipt ids
}
```

After ACK, rows are gone from the server.

### Drop own undelivered messages

```
DELETE /api/v1/chats/{chat_id}/outbox
```

Deletes pending ciphertext your user sent that recipients have not read yet.

---

## Message security model

| Layer | Protects against | Mechanism |
| --- | --- | --- |
| Transport | Passive sniffing, basic MITM on connections | HTTPS/WSS (reverse proxy) |
| Payload | Server, admins, DB leaks | E2E on clients; server stores/forwards only `ciphertext` |
| Identity | Wrong peer keys | Clients show `key_fingerprint` for each participant — verify out-of-band |
| Retention | Permanent archive | Server deletes after ACK; TTL purge for stale pending (default 30 days) |
| Session | Lost device | Short-lived JWT; PIN protects the app per product spec |

Client contract: `X25519 + crypto_box` (NaCl/libsodium). User public key is 32 bytes base64; ciphertext includes nonce+MAC, base64-encoded.

---

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `PIGEON_SECRET_KEY` | **must set** | JWT + admin cookie signing secret |
| `PIGEON_PUBLIC_URL` | `http://localhost:8000` | Base URL for invite links in admin UI |
| `PIGEON_DB_PATH` | `/data/pigeon.sqlite3` | SQLite file path (in container) |
| `PIGEON_EXPORTS_DIR` | `/exports` | JSON export directory |
| `PIGEON_JWT_TTL_HOURS` | `720` (~30 days) | Client JWT lifetime |
| `PIGEON_ADMIN_SESSION_TTL_HOURS` | `12` | Admin session lifetime |
| `PIGEON_MESSAGE_TTL_DAYS` | `30` | Undelivered message TTL |
| `PIGEON_MAX_CIPHERTEXT_BYTES` | `65536` | Max ciphertext size |
| `PIGEON_ADMIN_USERNAME` | — | Bootstrap admin username |
| `PIGEON_ADMIN_PASSWORD` | — | Bootstrap admin password |

---

## Local development without Docker

Requires Python 3.12+ (recommended). Python 3.14 may need recent pydantic wheels.

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1            # Windows
# source .venv/bin/activate           # Linux/macOS
pip install -r requirements.txt

$env:PIGEON_SECRET_KEY = "dev-secret"
$env:PIGEON_ADMIN_USERNAME = "admin"
$env:PIGEON_ADMIN_PASSWORD = "adminpass"

uvicorn app.main:app --reload
```

Smoke test:

```bash
$env:PYTHONPATH = "."
python tests\test_smoke.py
```

---

## Project layout

```
app/
  main.py              FastAPI entrypoint
  config.py            settings (env)
  db.py                async SQLAlchemy
  models.py            ORM
  schemas.py           Pydantic models
  security.py          JWT, bcrypt, tokens
  services.py          link activation, shared logic
  deps.py              FastAPI dependencies
  ws_manager.py        WebSocket registry
  cleanup.py           TTL purge task
  admin_setup.py       bootstrap admin
  templates.py         Jinja2 wiring
  routers/
    health.py          /health
    landing.py         /, /l/{token}
    links.py           link activation API
    me.py              /api/v1/me
    chats.py           messaging, poll, ack
    ws.py              /ws
    admin_auth.py      admin login/logout
    admin_ui.py        /admin HTML
    admin_export.py    /admin/api/export|import
  templates/           HTML
  static/admin.css

scripts/
  create_admin.py
  export_config.py

tests/
  test_smoke.py        ASGI end-to-end smoke test
```
