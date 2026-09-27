# Sideword Server

[![Tests](https://github.com/vladleesi/sideword-chat-server/actions/workflows/test.yml/badge.svg)](https://github.com/vladleesi/sideword-chat-server/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted messenger backend built with FastAPI and SQLite. It supports
invite-only personal and group chats, optional room passwords, expiring invites,
and an admin UI. Clients encrypt messages; the server relays ciphertext and
deletes it after acknowledgement, reading, or expiry.

The bundled browser client is for testing. The protocol has not been independently
audited. See the [security policy](SECURITY.md).

## Run locally

Requires Git and Python 3.12+. No frontend build is needed.

```sh
git clone https://github.com/vladleesi/sideword-chat-server.git
cd sideword-chat-server
python -m venv .venv
```

Activate the environment:

| Shell | Command |
| --- | --- |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Linux/macOS | `source .venv/bin/activate` |

Install dependencies:

```sh
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`: `Copy-Item .env.example .env` in PowerShell or
`cp .env.example .env` on Linux/macOS. Set `SIDEWORD_ADMIN_USERNAME` and replace
`SIDEWORD_ADMIN_PASSWORD`. Generate a signing secret:

```sh
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Paste the result into `SIDEWORD_SECRET_KEY` in `.env`, then start the server:

```sh
python -m scripts.serve_shared
```

Open these paths on **localhost, port 8000**:

| Path | Purpose |
| --- | --- |
| `/admin/links` | Sign in with the admin credentials from `.env`; create invites |
| `/client` | Test browser client |
| `/docs` | Interactive API reference |
| `/health` | Health and backend version |

Port **8001** serves the public API and client with administration blocked.
Both listeners bind to loopback. Stop with Ctrl+C. Keep `.env` out of Git.

To test a chat, create an invite and open it in two separate browser profiles
or a normal and private window. Activate both participants and compare peer key
fingerprints out of band. Share room passwords separately from invite links.
**Reset device** permanently removes that browser's keys and local history.

## Docker

Requires Git, Docker, and Compose v2. Clone the repository and configure `.env`
as above; skip the virtual environment and dependency installation. If Python
is unavailable, prefix the secret-generation command above with
`docker run --rm python:3.12-slim`.

```sh
docker compose up -d --build
```

Use the same paths on localhost, port **8000**. Data persists in the
`sideword-data` volume. Stop with `docker compose stop`, resume with
`docker compose start`. `docker compose down -v` deletes the database volume.
Before exposing the server publicly, follow the [deployment guide](docs/DEPLOYMENT.md).

## Documentation

- [Deployment](docs/DEPLOYMENT.md): settings, admin recovery, HTTPS, and the `docs/` site.
- [Invites](docs/INVITES.md): passwords, participant limits, and reconnects.
- [Client API](docs/API.md): encryption, delivery, and acknowledgement rules.
- [Backups and upgrades](docs/UPGRADING.md): exports, storage, and migrations.
- [Contributing](CONTRIBUTING.md): development setup, checks, and releases.
- [Changelog](CHANGELOG.md), [Security](SECURITY.md), [Code of conduct](CODE_OF_CONDUCT.md), and [MIT license](LICENSE).
