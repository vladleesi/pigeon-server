# Protected invites


In **Admin → Invite links → Create link**, choose **Generated password** or
**Custom phrase**. New custom values are 8–32 characters and are matched
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
