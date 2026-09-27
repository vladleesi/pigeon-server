# Client API

See `/docs` on the local administration listener for the complete schema.

For password-protected rooms, include `password` in activation. Persist a random
`resume_credential` before the first activation request so retries reuse the same
participant slot. See [protected invites](INVITES.md) for the complete admission,
rate-limit, expiration, and reconnection contract.


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

Flow: the client receives `<SIDEWORD_PUBLIC_URL>/l/<token>`, opens it in a
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
- Group links share a chat and optionally seal at their participant limit.
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
- Max ciphertext length: `SIDEWORD_MAX_CIPHERTEXT_BYTES` (default 64 KiB).
- `client_message_id` ties to local history and receipts.

### Receive messages and receipts

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
| Session | Lost device | Expiring JWT bound to an active invite |

The server contract requires a 32-byte base64 X25519 public key and treats each
ciphertext envelope as opaque base64 data. Production clients must agree on an
authenticated envelope format. The bundled test client uses
`X25519-2DH + HKDF-SHA-256 + AES-256-GCM`; NaCl/libsodium clients may instead use
`crypto_box` when all participants use that format.

---
