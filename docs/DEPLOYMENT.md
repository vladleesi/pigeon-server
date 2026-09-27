# Deployment

Start with the [README](../README.md) for local or Docker setup.

## Configuration

Set environment variables or use `.env`. Every setting below needs the
`SIDEWORD_` prefix; for example, `SIDEWORD_MESSAGE_TTL_DAYS=30`.

| Setting | Default / purpose |
| --- | --- |
| `SECRET_KEY` | Required random signing secret, at least 32 characters |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Create an admin on first startup |
| `PUBLIC_URL` | Invite origin; defaults to HTTP on localhost, port 8000 |
| `DB_PATH` | `./data/sideword.sqlite3`; `/data/sideword.sqlite3` in Docker |
| `EXPORTS_DIR` | `./exports`; `/exports` in Docker |
| `JWT_TTL_HOURS` | `720` |
| `ADMIN_SESSION_TTL_HOURS` | `12` |
| `MESSAGE_TTL_DAYS` | `30`; pending ciphertext lifetime |
| `MAX_CIPHERTEXT_BYTES` | `65536` |

Bootstrap credentials can be removed from `.env` after the admin exists.
To create an admin or reset its password, run the interactive command:

```sh
python -m scripts.create_admin --username admin
```

For Docker, use `docker compose exec sideword python -m scripts.create_admin --username admin`.

## Public access

Set `SIDEWORD_PUBLIC_URL=https://chat.example.com` to your public HTTPS origin
before starting the server. This setting controls generated invite links.

| Setup | Proxy target | Administration |
| --- | --- | --- |
| Shared Python runner | Loopback port 8001 | Blocked on 8001; available locally on 8000 |
| Docker Compose | Loopback port 8000 | Proxy must block `/admin` and all its subpaths, `/docs`, `/redoc`, and `/openapi.json` |

Configure an HTTPS reverse proxy or tunnel with WebSocket forwarding. Trust
forwarded scheme headers only from the actual proxy address. Protected invites
require HTTPS except during direct loopback development. Keep administration private.

A temporary tunnel can forward to port 8001. Update `SIDEWORD_PUBLIC_URL` and
restart the backend whenever the public origin changes. A named tunnel with a
personal domain provides a stable origin. Keep credentials and tunnel addresses
out of Git. See [protected invites](INVITES.md) for the trust model.

Compose uses service `sideword`, image `sideword-chat-server:latest`, and volume
`sideword-data`; exports are mounted to the local `exports/` directory.
`docker compose down` retains the database volume; adding `-v` removes it.
See [backups and upgrades](UPGRADING.md) before replacing a deployment.

## Landing page

`docs/index.html` is an independent static site with no build step, executable
JavaScript, or third-party fonts. GitHub Pages hosts this site, not the backend.
Preview it locally:

```sh
python -m http.server 8080 --bind 127.0.0.1 --directory docs
```

Open `/` on localhost, port **8080**. Assets use relative paths so the site also
works under a Pages project path.

After changes reach `main`, select a publishing source in **Settings > Pages**:

- **GitHub Actions**: run **Actions > Publish landing page > Run workflow** on
  `main`. The included workflow is manual-only and uploads the static assets and
  metadata, excluding Markdown guides.
- **Deploy from a branch**: select `main` and `/docs`. GitHub publishes the whole
  folder, including Markdown guides, after eligible pushes. The promotion job
  uses `GITHUB_TOKEN`, which does not trigger branch-based Pages builds; use the
  Actions publishing option for those updates.

See [GitHub's publishing-source guide](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
For a custom domain, configure Pages settings and DNS, then update the metadata below.

### Search and previews

The configured site URL is `https://vladleesi.dev/sideword-chat-server/`.
If the domain or project path changes, update absolute URLs in `index.html`
(canonical, Open Graph/X, and JSON-LD), `sitemap.xml`, and `robots.txt`.
Keep `social-card.png` publicly accessible and consistent with the landing copy.
Metadata describes the public site, never private chats or invites.

You can submit `sitemap.xml` to Google Search Console or Bing Webmaster Tools;
publishing it does not guarantee indexing. Crawlers read `robots.txt` only at
the origin root, so a copy under a Pages project path has no effect. Manage it
at the host root or use a custom domain; the page's robots meta tag still applies.
See [Google's placement rules](https://developers.google.com/crawling/docs/robots-txt/create-robots-txt).
