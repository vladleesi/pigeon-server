# Invites

## Create and share

In **Admin > Invite links > Create link**, choose a personal or group room:

- Personal rooms have two participant slots.
- Groups can have a limit of 2-1000 participants or remain unlimited.
- Choose no password, a generated 16-character password, or a custom phrase
  of 8-32 characters. Custom phrases match exactly, including case and whitespace.

The creator occupies a slot only after joining. Share passwords separately from
invite links. Only explicit activation claims a slot; opening a page or a link
preview does not.

The latest protected invite is saved encrypted in the same browser tab for up to
24 hours. Refreshes preserve it; closing the tab or clearing browser data can
lose it. Storage uses AES-GCM with a non-exportable IndexedDB key and a random
record ID in sessionStorage. Expired records are purged on the next access.
Passwords are never stored or exported in plaintext, or included in URLs.

## Join and reconnect

Call `POST /api/v1/links/{token}/activate` with `public_key`, optional
`display_name`, and `password` for a protected room. See the [API guide](API.md).

Before the first request, persist a separate random 32-byte `resume_credential`
for that invite, encoded as unpadded base64url (43 characters), alongside the
device identity. Retrying with it and the same public key reuses the participant
slot, even if the first response was lost. The server stores only its SHA-256
digest. The bundled client handles this automatically.

An existing valid bearer JWT can also reconnect without another password or
slot. A public key alone cannot authorize reconnecting. Losing both credentials
and the session does not free an occupied slot.

Admission uses a SQLite `BEGIN IMMEDIATE` transaction to prevent concurrent
joins from overfilling rooms. Full rooms seal against new participants while
existing participants can reconnect. Explicit revocation, deletion, participant
deactivation, and expiry invalidate access; a password or resume credential
cannot reopen an expired room.

## Password security

Protected creation and joining require HTTPS, except direct loopback development.
Follow the [deployment guide](DEPLOYMENT.md#public-access) for proxy configuration.

Passwords use server verification with salted scrypt, not PAKE. The application
server and TLS terminator see submitted passwords in memory and must be trusted.
Do not enable request-body logging. Passwords gate admission; they are not message
encryption keys and do not replace out-of-band peer key verification.

The implementation uses OpenSSL-backed scrypt with N=2^17, r=8, p=1, a random
16-byte salt, and a 32-byte result, following the
[OWASP scrypt profile](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
Generated passwords contain 96 random bits.

Failed guesses are limited to five per invite per five-minute fixed window,
persisted in SQLite. Further attempts return 429 with `Retry-After`; changing IP
addresses does not reset the limit. Anyone with an invite can temporarily exhaust
its guess budget, but authenticated reconnects remain available.

Startup adds password and retry metadata without changing unprotected invites.
Admin exports preserve verifiers and retry metadata, never plaintext passwords;
see [backups](UPGRADING.md#backups).
