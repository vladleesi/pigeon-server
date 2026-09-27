# Sideword Server

[![Tests](https://github.com/vladleesi/sideword-chat-server/actions/workflows/test.yml/badge.svg)](https://github.com/vladleesi/sideword-chat-server/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Backend version: 0.2.0.** A link-only messenger backend with personal and group
rooms, optional room passwords, expiring invites, encrypted message relay, and
an administration UI. The included browser client is for testing; the protocol
has not had an independent security audit. Pending ciphertext is deleted after
ACK/read or TTL, rather than retained as a permanent archive.

## Run locally

Requires Python 3.12+; Node.js 22 is used for frontend tests.

```sh
git clone https://github.com/vladleesi/sideword-chat-server.git
cd sideword-chat-server
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` on Windows or
`source .venv/bin/activate` on Linux/macOS, then:

```sh
python -m pip install -r requirements-dev.txt
```

Copy `.env.example` to `.env` (`Copy-Item .env.example .env` on PowerShell;
`cp .env.example .env` elsewhere). Set `SIDEWORD_SECRET_KEY` to a random secret,
plus `SIDEWORD_ADMIN_USERNAME` and `SIDEWORD_ADMIN_PASSWORD`. Generate the secret:

```sh
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -m scripts.serve_shared
```

- Administration: <http://127.0.0.1:8000/admin/links>
- Browser test client: <http://127.0.0.1:8000/client>
- API reference: <http://127.0.0.1:8000/docs>
- Health and backend version: <http://127.0.0.1:8000/health>

The shared runner keeps administration on port **8000** and exposes a restricted
public listener on **8001**, with one shared WebSocket registry. Stop with Ctrl+C.
For local development with reload, use `uvicorn app.main:app --reload` instead;
that single listener also exposes administration.

## Docker

Requires Docker and Compose v2. Configure `.env` as above, then:

```sh
docker compose up -d --build
docker compose stop                 # retain data
docker compose start
```

The service is `sideword`, the image is `sideword-chat-server:latest`, and the persistent
volume is `sideword-data`. `docker compose down` retains that volume; adding `-v`
deletes it. The Compose listener is local-only on port 8000 and includes admin
routes. Configure the reverse proxy to deny `/admin`, `/docs`, `/redoc`, and
`/openapi.json` on a public domain. For PC sharing, use the restricted runner above.

## Personal domain or temporary sharing

Set `SIDEWORD_PUBLIC_URL=https://chat.example.com` before starting the server.
Point your domain at an HTTPS reverse proxy or a named tunnel forwarding to the
restricted listener on `127.0.0.1:8001`. Enable WebSocket forwarding. Trust forwarded
scheme headers only from the actual proxy address; never trust arbitrary clients.
TLS is required for protected invites except on direct loopback development.
Keep administration private on port 8000.

A temporary tunnel may forward to the same restricted port. Put its current HTTPS
origin in `SIDEWORD_PUBLIC_URL` and restart the server when that origin changes.
A named tunnel with your domain avoids temporary address changes. Keep credentials,
tunnel addresses, and `.env` out of Git.

## Invites and browser use

Create a personal (two participants) or group invite in the admin UI. Group limits
are optional. Choose no password, a generated 16-character password, or a custom
8?32-character phrase. Share the password separately from the link.

Only explicit activation claims a participant slot. Link previews and page opens
do not. Authenticated reconnects reuse the same slot, and full rooms seal against
new participants. Expiration, revocation, and deletion invalidate access.

The latest protected invite is saved encrypted in the same browser tab for up to
24 hours, including across refreshes. Closing the tab or clearing browser data can
lose it. Password admission uses HTTPS and salted scrypt, not PAKE: the server and
TLS terminator must be trusted. See [protected invites](docs/INVITES.md).

Test a chat in separate browser profiles or a normal and private window. Compare
peer key fingerprints out of band. Device keys and local history remain in the
browser; **Reset device** permanently removes them. See the [client API](docs/API.md)
for activation, resumable joins, WebSocket authentication, polling, and ACK rules.

## Configuration

All environment settings use the `SIDEWORD_` prefix and can be set in `.env`.

| Setting | Default / purpose |
| --- | --- |
| `SECRET_KEY` | Required random JWT/admin signing secret |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Bootstrap admin on first startup |
| `PUBLIC_URL` | `http://localhost:8000`; origin for generated invitations |
| `DB_PATH` | `./data/sideword.sqlite3`; `/data/sideword.sqlite3` in Docker |
| `EXPORTS_DIR` | `./exports`; `/exports` in Docker |
| `JWT_TTL_HOURS` | `720` |
| `ADMIN_SESSION_TTL_HOURS` | `12` |
| `MESSAGE_TTL_DAYS` | `30`; pending ciphertext TTL |
| `MAX_CIPHERTEXT_BYTES` | `65536` |

Bootstrap credentials can be removed after the first admin is stored. To create
or reset an admin interactively:

```sh
python -m scripts.create_admin --username admin
# Docker: docker compose exec sideword python -m scripts.create_admin --username admin
```

## Backup and upgrades

Use **Admin ? Export** to download or import configuration. Bundles contain users,
chats, chat participants, active invite tokens, and password verifiers, but no
message history. Protect these files as secrets. Full replacement removes the
existing configuration. CLI export:

```sh
docker compose exec sideword python -m scripts.export_config --output /exports/sideword-config.json
```

Back up the database before upgrading and follow the
[upgrade steps](docs/UPGRADING.md). Startup applies additive SQLite schema migrations.

## Static landing page

The independent landing site is `docs/index.html`, with `site.css` and `mark.svg`.
It uses no executable JavaScript, fonts from third parties, dependencies, or build step.
Static JSON-LD describes the site and source repository for crawlers.
Preview it with `python -m http.server 8080 --bind 127.0.0.1 --directory docs`,
then open `http://127.0.0.1:8080`. Its relative assets work under a Pages project path.
The setup CTA links to this repository; GitHub Pages does not host the chat backend.

After changes reach `main`, choose a publishing method in **Settings → Pages**:

- **Deploy from a branch**: select `main` and `/docs`, then save. GitHub publishes
  the whole folder, including its public Markdown documentation. Subsequent eligible
  pushes rebuild the site; this is not a manual-only deployment mode.
- **GitHub Actions** (optional): run **Actions → Publish landing page → Run workflow**
  on `main`. The included workflow is manual-only and uploads only the static site
  assets, metadata, and crawler files.

The existing promotion job pushes with `GITHUB_TOKEN`, which does not trigger a
branch-based Pages build. Use the optional manual workflow when publishing those
updates. See [GitHub's publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
No repository settings are changed automatically. For a custom domain, configure
it in Pages settings and update DNS as GitHub instructs, then update the absolute
metadata URLs described below.

### Search and link previews

The landing page includes a canonical URL, Open Graph/X preview metadata, a PNG
social card, JSON-LD, and a one-page sitemap. The configured public URL is
`https://vladleesi.dev/sideword-chat-server/`. Before using a different domain or
repository path, update the absolute URLs in `docs/index.html`, `docs/sitemap.xml`,
and `docs/robots.txt`. Preview images must be publicly accessible after deployment.

Submit the deployed `sitemap.xml` URL in Google Search Console or Bing Webmaster
Tools if desired; these files do not submit the site or guarantee indexing.
`robots.txt` is effective only at the origin root: on a project Pages URL it is
under a subdirectory and is not used by crawlers. Manage the host-root robots file
separately, or use a custom domain where these files are served at the root.
The page-level robots tag still allows indexing. See
[Google's robots.txt placement rules](https://developers.google.com/crawling/docs/robots-txt/create-robots-txt).
These files describe the public landing site, not private chats or invite URLs.

## Development and releases

Make changes on a feature or fix branch, never directly on `main`. GitHub Actions
runs lint, Python/JavaScript tests, and a Docker build on pushes to every branch
except `main`, and on pull requests targeting `main`. Pushes to `main` do not run CI.
After both check jobs pass on a push to the maintainer's `develop` branch, GitHub
Actions fast-forwards and pushes that exact tested commit to `main`. Other branches
and pull requests run checks only. Promotion skips outdated runs and rejects
diverged history; integrate changes on the source branch and rerun checks to retry.
The promotion job uses the built-in GitHub token with `contents: write`; repository
rules must permit its push to `main`. This workflow does not deploy the application.

The single backend version source is `app/version.py`, exposed in `/health` and
OpenAPI. Use semantic versions: patch for fixes, minor for features/pre-1.0 breaking
changes, and major for a stable breaking release. Update [CHANGELOG.md](CHANGELOG.md)
and create a matching `vX.Y.Z` tag when publishing a release. API `/api/v1` and
cryptographic envelope versions are independent of backend release numbers.

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks, [SECURITY.md](SECURITY.md) for
private reports, and [LICENSE](LICENSE) for the MIT license.
