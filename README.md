# Pigeon Server

[![Tests](https://github.com/vladleesi/pigeon-server/actions/workflows/test.yml/badge.svg)](https://github.com/vladleesi/pigeon-server/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Backend for a link-only messenger. The server:

- Creates and revokes **personal** (1:1) and **group** invite links.
- Client sessions are bound to the invite that issued them; manually revoking
  that invite invalidates its JWTs and closes connected WebSockets.
- Accepts clients who activate those links.
- Supports optional generated passwords or custom room phrases, with authenticated
  participant limits and sealed rooms.
- Relays **client-encrypted** messages end-to-end — the server only sees ciphertext and metadata.
- Does **not** keep a permanent archive: delivered and read messages are removed.
- Provides a minimal web admin UI for monitoring and exporting/importing configuration to another host.

> [!IMPORTANT]
> This repository is primarily the **server**. It includes a lightweight browser
> client at `/client` for local and interoperability testing, not as an audited
> end-user application. Production clients should implement the API below.

## Project status

Pigeon Server is pre-1.0 software intended for experimentation and community
development. Its cryptographic protocol and implementation have not received an
independent security audit. Do not rely on it for high-risk communications.

## Optional room passwords (Sideword invites)

In **Admin → Invite links → Create link**, choose **Generate a password** or
**Custom phrase or password**. New custom values are 8–32 characters and are matched
exactly, including whitespace and case. Generated passwords are 16 characters (96 random bits). Both generated and custom
passwords are shown in the creation panel. The latest invite is saved encrypted
with AES-GCM in browser IndexedDB using a non-exportable key, with only a random
record ID in sessionStorage. It survives refreshes in the same tab for up to 24
hours; closing the tab or clearing browser data can lose recovery. Expired records
are purged when invite storage is next accessed. Share the password separately
from the invitation URL. No plaintext room password is stored, exported, placed
in a URL, or saved in plaintext by the browser client.

The authentication design is **HTTPS plus a salted scrypt verifier**, using the
standard library's OpenSSL-backed scrypt implementation with N=2^17, r=8, p=1,
a 16-byte random salt, and a 32-byte result. This follows the
[OWASP scrypt profile](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
It is a server-verified alternative, **not PAKE**: the application server and TLS
terminator (including a tunnel provider) see submitted passwords in memory and
must be trusted. It does not protect against a malicious server or replace peer
key verification. Passwords gate admission; they are not message encryption keys.
Do not enable request-body logging at the application or proxy.

Protected creation and joining require HTTPS, except direct local loopback for
development. Configure the server to trust forwarded scheme headers only from
your actual reverse proxy; arbitrary forwarded headers are not accepted by the
application. Keep the shared public tunnel on port 8001 so administration stays
local. Existing message encryption and non-exportable browser keys are unchanged.

Only an explicit `POST /api/v1/links/{token}/activate` can claim a slot. Include
`public_key`, optional `display_name`, and `password` for a protected room. GETs,
previews, page refreshes, and opening the browser client never claim slots.
Personal rooms have exactly two slots. Groups optionally accept a participant
limit of 2–1000; without a limit they remain open. The creator counts as a
participant only when they explicitly join.

Clients should generate a separate 32-byte cryptographically random bearer
`resume_credential` per invite, encode it as unpadded base64url (43 characters),
and persist it with their device identity **before** the first activation POST.
The bundled browser client does this automatically. Retrying with that credential
and the same public key returns the same participant even if the original HTTP
response was lost. Only its SHA-256 digest is stored on the server. An existing
valid bearer JWT can also reconnect without another password or slot. A public
key alone never authorizes reconnection. Losing both the browser's credentials
and its session does not free a sealed slot.

Admission runs under a SQLite `BEGIN IMMEDIATE` transaction, so concurrent joins
and multiple server processes cannot overfill rooms. Once the limit is reached,
the invitation seals against new participants while existing participants can
reconnect. Explicit revocation, deletion, participant deactivation, and expiry
still invalidate access. Failed guesses are limited to five per invite per
five-minute fixed window, persisted in SQLite; subsequent attempts return 429
with `Retry-After`. Rotating IP addresses does not reset this limit. Someone with
an invite can temporarily exhaust its guess budget; authenticated reconnects
remain available.

Startup adds nullable verifiers and retry metadata to existing databases without
changing unprotected invites. Administrative exports preserve verifiers and retry
metadata, never plaintext passwords; treat these backups as sensitive. Existing
expiration checks apply to both admission and resumption, so temporary rooms
cannot be reopened with a password or retry credential after their deadline.

---

## Quick start

### 1. Prerequisites

Docker 24+ and Docker Compose v2.

```bash
git clone https://github.com/vladleesi/pigeon-server.git
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
- `http://localhost:8000/client` — lightweight encrypted test client.
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

### Bundled test web client

The dependency-free client at `/client` can activate invite links and exchange
encrypted messages with another copy of itself. It uses browser Web Crypto with
a sender-static plus ephemeral X25519 construction, HKDF-SHA-256, and
AES-256-GCM. Peer key fingerprints are shown for out-of-band verification. Its
private key is stored as a
non-exportable `CryptoKey` in IndexedDB. Local history is encrypted with a
separate non-exportable AES-GCM key and survives page refreshes until **Reset
device** is used. The page ships with a restrictive Content Security Policy and
does not load third-party code.

Use it on `localhost` or behind HTTPS. It is intended for testing, has not been
independently audited, and its envelope format is not compatible with NaCl
`crypto_box` clients without an interoperability layer.

To test a personal chat, create an invite in the admin UI, then open its landing
page once in a normal browser window and once in a private window. Activate each
side with a different display name, compare the full peer fingerprints out of
band, and send a message. Use **Reset device** when finished; resetting destroys
the local private key and makes that browser identity unrecoverable.

Flow: the client receives `https://<PIGEON_PUBLIC_URL>/l/<token>`, opens it in a
compatible client, and calls HTTP. The invite landing page links to the bundled
test client.

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
   Browser clients can avoid putting the JWT in the URL by requesting the
   `pigeon.v1` WebSocket subprotocol and immediately sending
   `{"type":"auth","token":"<JWT>"}` as the first frame. Query and legacy
   authentication-subprotocol clients remain supported.

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

The server contract requires a 32-byte base64 X25519 public key and treats each
ciphertext envelope as opaque base64 data. Production clients must agree on an
authenticated envelope format. The bundled test client uses
`X25519-2DH + HKDF-SHA-256 + AES-256-GCM`; NaCl/libsodium clients may instead use
`crypto_box` when all participants use that format.

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
pip install -r requirements-dev.txt

$env:PIGEON_SECRET_KEY = "local-dev-only-secret-at-least-32-characters"
$env:PIGEON_ADMIN_USERNAME = "admin"
$env:PIGEON_ADMIN_PASSWORD = "adminpass"

uvicorn app.main:app --reload
```

Smoke test:

```bash
$env:PYTHONPATH = "."
python -m pytest
```

The smoke suite uses a temporary database and exercises admin login, personal
and group invites, encrypted-message relay, read receipts, and export/import.

---

## Temporary sharing from your PC

The client shows a countdown for invites with an expiration date, using server
time (and the earlier JWT deadline when applicable). Invites without a deadline
hide the countdown; the configured JWT lifetime still applies to authentication.
Expired invites also end access for their issued sessions.

In the admin links page, **Restore** removes an explicit revocation while
preserving the activation limit. A fully used personal link remains closed to
new participants. Existing clients can use **Reconnect saved session** after
restoration; expired links require a new invite.

The admin pages support individual deletion, checkbox selection for bulk deletion
of users, links and chats, and cleanup of blocked users or revoked/expired links.
All destructive actions use a shared confirmation dialog. Deleting chats also
removes their memberships, queued messages and receipts, and invalidates their
invite links. Deleting users removes memberships,
pending messages and receipts. Deleting links invalidates their sessions and
removes the invite token and note; an internal tombstone prevents ID reuse from
reviving old JWTs. Fully used, non-revoked links are excluded from bulk cleanup.

Run both listeners in one process so message delivery and revocation share the
same connection registry:

```bash
python -m scripts.serve_shared
```

Administration is available at `http://127.0.0.1:8000/admin/links`.
Forward an HTTPS tunnel only to `http://127.0.0.1:8001`; that listener allows
the client, invitations, messaging API, WebSocket, static assets, and health
check, while blocking administration and API documentation.

For a temporary Cloudflare tunnel, run
`cloudflared tunnel --url http://127.0.0.1:8001`.
Set `PIGEON_PUBLIC_URL` to the assigned HTTPS origin before starting the Python
process so generated invitations contain the correct public address. Keep the
PC awake during testing; restarting a temporary tunnel changes its address.
Store actual addresses and credentials only in local configuration.

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
    client.py          /client test UI
    admin_auth.py      admin login/logout
    admin_ui.py        /admin HTML
    admin_export.py    /admin/api/export|import
  templates/           HTML
  static/admin.css     admin styles
  static/client.css    test client styles
  static/client.js     browser crypto + client behavior

scripts/
  create_admin.py
  export_config.py
  serve_shared.py      local admin + restricted public listener

tests/
  test_smoke.py        ASGI end-to-end smoke test
```

---

## Contributing

Bug reports, feature proposals, documentation improvements, and code changes are
welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
Please report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

Pigeon Server is available under the [MIT License](LICENSE).
