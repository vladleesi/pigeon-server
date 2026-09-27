# Client API

See `/docs` on the local administration listener (port 8000) for the complete
schema. Send JSON bodies with `Content-Type: application/json`. After activation,
HTTP API requests require `Authorization: Bearer <JWT>`.

For password-protected rooms, include `password` in activation. Persist a random
`resume_credential` before the first activation request so retries reuse the same
participant slot. See [invites and chats](../README.md#invites-and-chats) for
room limits, password handling, expiration, and browser use.

## Bundled test web client

The dependency-free client at `/client` can activate invite links and exchange
encrypted messages with another copy of itself. It uses browser Web Crypto with
a sender-static plus ephemeral X25519 construction, HKDF-SHA-256, and
AES-256-GCM. Fingerprints are calculated locally for out-of-band verification;
the first observed peer key is pinned locally and later changes block use. This
is trust on first use, not protection against initial substitution or malicious
server-delivered JavaScript. The protocol has no recipient forward secrecy or
post-compromise recovery. See the [v1 wire contract](PROTOCOL.md) and
[security review / migration plan](SECURITY_REVIEW.md). Its private key is stored
as a non-exportable `CryptoKey` in IndexedDB. Local history is encrypted with a
separate non-exportable AES-GCM key and survives page refreshes until **Reset
device** is used. The page ships with a restrictive Content Security Policy and
does not load third-party code.

Use it on `localhost` or behind HTTPS. It is intended for testing, has not been
independently audited, and its envelope format is not compatible with NaCl
`crypto_box` clients without an interoperability layer.

For a two-browser walkthrough, see [invites and chats](../README.md#invites-and-chats).
Invite links use `/l/{token}` and open a landing page that links to `/client`.

## Activate link

```
POST /api/v1/links/{token}/activate
Content-Type: application/json

{
  "public_key": "<base64, 32 bytes, X25519>",
  "display_name": "Alice",
  "resume_credential": "<43-character base64url credential>"
}
```

`display_name` is optional; include `password` for protected rooms. The response
contains a client JWT, user identity, and chat participants:

```text
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

- Personal links allow **two participants**; once full, they seal against new joins.
  Authenticated reconnects reuse the existing slot.
- Group links share a chat and optionally seal at their participant limit.
- If the device already sends `Authorization: Bearer ...`, activation **does not** create a new user — it adds the current user to the new chat/group.

### Admission and retries

Generate a separate cryptographically random 32-byte `resume_credential` for
each invite, encode it as unpadded base64url (43 characters), and persist it
with the device identity before activation. A retry with the same credential
and public key reuses the participant even if the original response was lost;
the server stores only the credential's SHA-256 digest. A valid bearer JWT can
also reconnect without another password or slot, but a public key alone cannot.

Admission uses a SQLite `BEGIN IMMEDIATE` transaction to prevent concurrent
joins from overfilling rooms. Failed password guesses are limited to five per
invite per five-minute fixed window, persisted in SQLite. Further attempts
return 429 with `Retry-After`; changing IP addresses does not reset the limit.
Anyone holding an invite can exhaust its guess budget, while authenticated
reconnects remain available. Expired or revoked access cannot be restored with
a password or resume credential.

Password verification uses OpenSSL-backed scrypt with N=2^17, r=8, p=1, a random
16-byte salt, and a 32-byte result. Generated passwords contain 96 random bits.
This is server verification over HTTPS, not PAKE: the server and TLS terminator
must be trusted. Passwords are never included in URLs or stored or exported in
plaintext. Admin exports preserve verifiers and retry metadata; startup adds
these fields without changing unprotected invites.

The admin UI's invite recovery uses AES-GCM with a non-exportable IndexedDB key
and a random record ID in sessionStorage. It preserves the latest protected
invite in the same tab for up to 24 hours and purges expired records on access.

## Profile and chats

```
GET /api/v1/me
Authorization: Bearer <JWT>
```

Returns the current user, chats, and each participant’s public key (used to encrypt outbound envelopes).

## Send message

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
- `ciphertext` is opaque base64 data. All participants must agree on an
  authenticated encryption format; see the security model below.
- Max ciphertext length: `SIDEWORD_MAX_CIPHERTEXT_BYTES` (default 64 KiB).
- `client_message_id` ties to local history and receipts.

## Receive messages and receipts

1. **WebSocket** (recommended):

   ```
   GET /ws?token=<JWT>
   ```

   The `hello` frame includes backlog (offline messages and receipts); then `message` and `read` events stream live.
   Browser clients can avoid putting the JWT in the URL by requesting the
   `sideword.v1` WebSocket subprotocol and immediately sending
   `{"type":"auth","token":"<JWT>"}` as the first frame. Query and legacy
   authentication-subprotocol clients remain supported.

2. **Polling fallback**:

   ```
   GET /api/v1/poll
   Authorization: Bearer <JWT>
   ```

   Returns `messages` and `read_receipts` arrays.

## Mark read

```
POST /api/v1/chats/{chat_id}/read
{ "client_message_ids": ["m-1", "m-2"] }
```

The ciphertext row is removed from the server. A read receipt is created for the sender (poll/ws); after ACK it is deleted.

## Acknowledge server-side deletion

Authenticate, decrypt, and persist messages locally before acknowledging them.
Keep failures retryable and serialize incoming processing.

```
POST /api/v1/ack
{
  "message_ids": [123, 124],   // fetched and persisted locally
  "read_ids":    [45]          // read receipt ids
}
```

After ACK, rows are gone from the server. SQLite row IDs may be reused: deduplicate
messages by chat, sender, and client message ID; receipts by chat, reader, and
client message ID. Treat timestamps without a timezone as UTC when ordering history.

## Drop own undelivered messages

```
DELETE /api/v1/chats/{chat_id}/outbox
```

Deletes pending ciphertext your user sent that recipients have not read yet.

## Message security model

| Layer | Protects against | Mechanism |
| --- | --- | --- |
| Transport | Passive sniffing, basic MITM on connections | HTTPS/WSS (reverse proxy) |
| Payload | Server, admins, DB leaks | E2E on clients; server stores/forwards only `ciphertext` |
| Identity | Later changes to a known peer key | Test client computes SHA-256 fingerprints locally and pins first use; verify full fingerprints out-of-band |
| Retention | Permanent archive | Server deletes after ACK; TTL purge for stale pending (default 30 days) |
| Session | Continued access after expiry/revocation | Expiring JWT bound to a valid invite; HTTP checks and WS checks before pushes and while idle |

The server contract requires a 32-byte base64 X25519 public key and treats each
ciphertext envelope as opaque base64 data. Production clients must agree on an
authenticated envelope format. The bundled test client uses
`X25519-2DH + HKDF-SHA-256 + AES-256-GCM`; NaCl/libsodium clients may instead use
`crypto_box` when all participants use that format.

The server's `key_fingerprint` is a convenience field, not an independent trust
anchor. Neither protocol v1 nor `crypto_box` provides a session ratchet. A server
that substitutes keys before first use or serves malicious client code remains
outside the protection of local pinning. Default JWT lifetimes are unchanged;
the [review](SECURITY_REVIEW.md) describes renewal and migration requirements.
