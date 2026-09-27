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
Reload the client to load both scripts. No identity key, JWT, invite credential,
ciphertext, or local-history migration is required. New
`peer:` records hold local first-use fingerprint pins in the existing IndexedDB
store. Older client code ignores them; rolling back loses pin enforcement.
Verify existing peers out of band on the first upgraded use. Unexpected changed
keys block use; do not reset the device or delete history to dismiss the warning.

WS sessions are now checked before delivery and periodically while idle. Expired
or revoked credentials may therefore disconnect earlier than with the old client
ping-dependent behavior. Supported authentication transports and default TTLs
are unchanged. See [security review](SECURITY_REVIEW.md) for the separate ratchet
and renewable-session migration plan.

## Exact delivery acknowledgements

Update the server before reloading browser clients. Startup adds `delivery_id`
columns and unique indexes to pending messages and receipts, backfilling a random
identity for each existing row. Existing ciphertext and timestamps remain intact;
later startups preserve the identifiers. All server writers must be upgraded
together; running old and new server code against the same database is unsupported.
SQLite 3.35 or newer is required for atomic `DELETE ... RETURNING` operations.

Polling and WS payloads include the new field. Old clients can still use legacy
HTTP endpoints. The upgraded browser requires exact endpoints and leaves failed
acknowledgements retryable when connected to an older server. Do not downgrade
only the server underneath upgraded clients. A server-code rollback requires a
compatible pre-upgrade database backup and matching client code; it loses later
state and the new delivery protections.

Full database backups preserve delivery IDs; configuration exports omit queued
messages and receipts. A restored backup can contain previously acknowledged
deliveries. Clients must still deduplicate local history and acknowledge restored
rows after confirming persistence. Delivery IDs expire or are deleted with their message/receipt rows. The bounded
send ledger introduced below survives those deletions until its retry deadline.

## 0.3.2 client session and recovery hardening

Update the server using the normal controlled deployment procedure. There is no
schema or client migration. New renewable sessions are capped by invite expiry;
existing sessions retain their stored lifetime but remain subject to the current
invite and user checks. A concurrent revocation, deactivation, invite expiry or
legacy migration cutoff now rejects session issuance with 401. Admission may
already have committed a slot; retain the saved invite resume credential.
Rollback restores the issuance race and inaccurate deadline responses.

Isolated automated restore tests exercise the recovery procedure below, including
the failure of either step on its own. They do not validate an operator's actual
backup infrastructure or a historical server/client rollback.

## 0.3.1 admin login hardening

Update the server using your normal controlled deployment procedure. No database
schema, client, or encryption protocol migration is required; valid admin sessions
keep working. Password resets reject in-flight logins verified against the old
password and continue to revoke sessions issued before the reset commits.
An affected login returns the normal invalid-credentials error; sign in with the
new password. Session issuance now enforces the global 1,000-session cap across
workers and removes expired admin sessions without waiting for hourly cleanup.
Rolling back server code restores the login/reset race and non-atomic cap.

## 0.3.0 delivery/session hardening rollout

1. Stop all backend writers and make a private, consistent database backup.
2. Deploy the new server and browser together. Startup adds retry, session,
   rotation-evidence, and login-limit tables. Existing ciphertext/history stays
   readable. No ratchet or v1 ciphertext migration is involved.
3. Admins must log in again: pre-registry admin JWTs are rejected. Admin form/API
   cookie clients must fetch and submit CSRF tokens. Password resets revoke
   existing admin sessions. All administrative/schema routes are local-only by
   default; use the local listener or an SSH-forwarded loopback endpoint.
4. Migrate clients to renewable sessions and exact ACK/read. Verify saved refresh
   proposals, offline recovery, duplicate sends and concurrent tabs in your
   release QA. The browser requires Web Locks; it fails closed without them.
5. After every client has migrated, set `SIDEWORD_ALLOW_LEGACY_ACK=false` and a
   fixed UTC `SIDEWORD_LEGACY_TOKEN_DEADLINE`. Leaving compatibility enabled leaves
   the legacy risks open. Do not silently shorten the retry window for queued
   outboxes; clients have already persisted their original retry deadlines.

Default retry metadata retention is 30 days even if ciphertext was read earlier.
Admin sessions expire after 12 hours; client sessions after 30 days; refresh-use
hashes remain until session expiry. Login-limit records expire after one minute.
Hourly cleanup removes expired records; startup also schedules cleanup. Users,
room metadata and invite resume credentials keep their existing lifecycle. There
is no automatic destructive metadata purge. Full backups include these records;
configuration exports omit the retry/session ledger and cannot replace backups.

Keep database/configuration backups encrypted with host-controlled keys, restrict
file/volume access to the service/operator, and enforce a separate backup deletion
schedule. Logical SQL deletion does not erase old snapshots, WAL or free pages.
Treat restoring an old database as rolling authentication state back: before
reopening it, rotate the signing secret and remove `refresh_uses`, `client_sessions`
and `admin_sessions` in one database transaction while all writers remain stopped.
Deploy the new secret consistently to every worker before reopening access.
Rotating only the secret leaves restored refresh credentials usable; clearing only
session tables leaves legacy JWTs usable. Restoring without either step can revive
both client and admin sessions that were revoked after the snapshot.
Reapply subsequent invite revocations, user deactivations and admin password resets:
the snapshot also rolls those records back, and session cleanup does not invalidate
saved invite resume credentials or change restored passwords. Clients with still
authorized invites recover through their saved invite credentials.
Later send deduplication evidence absent from the backup is
unrecoverable; do not blindly replay outboxes across a restoration. Rollback to
older server code requires the matching pre-upgrade backup/client and loses the
new protections and intervening state. Exercise this procedure with your own isolated
backup copy before production recovery; the automated fixture does not validate
your storage, secrets distribution, proxy, or historical client/server versions.

HTTPS/WSS is required outside loopback. The application does not trust arbitrary
forwarded headers; the shared runner uses `SIDEWORD_TRUSTED_PROXY_IPS` (explicit
IP list), and standalone Uvicorn/Docker uses `FORWARDED_ALLOW_IPS` in its process
environment. Never set either to `*`. Container-to-proxy addresses may differ
from localhost; configure the actual trusted peer before exposing the service.
Set HSTS at the TLS terminator after validation. Continue blocking admin/schema
paths at the proxy and on the shared listener even with application checks.

The defaults in `.env.example` bound request bytes, request rate, pending message
count/bytes, receipts, retry records, users and WS connections. HTTP bodies have a
15-second read deadline. Admission KDFs are serialized by SQLite; login attempts
are persisted per hashed account/IP plus a global window. IP/connection/frame
limits are per process and reset on restart. Add gateway/global rate, connection,
header and idle-time limits, plus container memory/CPU/disk limits. Keep request,
credential, query-string and body logging disabled/redacted at every proxy.
The shared runner and Docker suppress raw access/WS INFO logs; SQL exceptions
hide parameter values. Existing upstream logs are not automatically erased.
