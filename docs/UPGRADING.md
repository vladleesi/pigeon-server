# Backend upgrades

1. Read `CHANGELOG.md` for the target release and back up any data you want to keep.
2. Stop the server before replacing application files or moving its SQLite database.
3. Update the application and install its pinned dependencies, or rebuild the Docker image.
4. Review `.env.example` for configuration changes. All settings use `SIDEWORD_`.
5. Start the server. Startup applies additive SQLite schema migrations.
6. Check `/health` for the expected backend version and reload browser clients.

The default database is `data/sideword.sqlite3` locally and
`/data/sideword.sqlite3` in Docker, using the `sideword-data` volume.
The browser client uses `sideword-test-client` storage and `sideword-web-v1`
encryption domain separation. Device identity and local history are scoped to
the browser origin. Clearing site data removes the local identity and history;
create fresh invites if participant slots were already filled.

Backend releases use `app/version.py`; the HTTP API remains `/api/v1`.
Make changes on a feature or fix branch, never directly on `main`. Run the checks
in `CONTRIBUTING.md` and wait for CI on the exact commit before merging into `main`.
