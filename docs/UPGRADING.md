# Backups and upgrades

## Backups

For a database backup, stop the server and copy its SQLite database and any
adjacent `-wal` and `-shm` files before restarting. The default database path is
`data/sideword.sqlite3` locally or
`/data/sideword.sqlite3` in Docker's `sideword-data` volume. Preserve `.env`
securely with the backup, including the signing secret.

For configuration transfer, use **Admin > Export** at `/admin/export-ui`.
Export/import bundles contain users, chats, participants, invite tokens,
password verifiers, and retry metadata, but no message history or admin accounts.
Treat them as secrets. Full replacement removes existing configuration and
pending deliveries. The CLI exporter omits protected-invite and resume
metadata; use the admin export for configuration transfer.

Browser keys and decrypted history are stored separately in the browser,
scoped to its origin. A server backup does not recover them. Clearing site data
or using **Reset device** removes them; create fresh invites if the old
participant slots were already filled.

## Upgrade

1. Read the target release's [changelog](../CHANGELOG.md).
2. Stop the server and back up the database and configuration as above.
3. Update application files to the target release.
4. Review `.env.example` for changed settings, preserving your existing secrets.
5. For Python, activate the virtual environment, run
   `python -m pip install -r requirements.txt`, then `python -m scripts.serve_shared`.
   For Docker, run `docker compose up -d --build`.
6. Check `/health` on the local admin listener for the expected version, then
   reload browser clients.

Startup applies additive SQLite schema migrations. Keep the backup until the
upgrade is verified. Backend release numbers, API `/api/v1`, and browser
encryption format versions are independent; see [contributing](../CONTRIBUTING.md#pull-requests-and-releases)
for the release process.

## Client security hardening (v1-compatible)

The protocol module must be deployed alongside `client.js` and `client.html`.
Reload the client to load both scripts. No server schema, identity key, JWT,
invite credential, ciphertext, or local-history migration is required. New
`peer:` records hold local first-use fingerprint pins in the existing IndexedDB
store. Older client code ignores them; rolling back loses pin enforcement.
Verify existing peers out of band on the first upgraded use. Unexpected changed
keys block use; do not reset the device or delete history to dismiss the warning.

WS sessions are now checked before delivery and periodically while idle. Expired
or revoked credentials may therefore disconnect earlier than with the old client
ping-dependent behavior. Supported authentication transports and default TTLs
are unchanged. See [security review](SECURITY_REVIEW.md) for the separate ratchet
and renewable-session migration plan.
