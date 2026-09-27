# Security review and incremental migration plan

Reviewed 2026-09-27. This is a source review with regression tests, not an
independent audit or proof that the application has no vulnerabilities.

## Decision and threat model

Keep the existing v1 protocol, APIs, identities, JWTs, invite retry credentials,
group fanout, delivery rules, and encrypted browser history. Do not roll out a
ratchet, new signing identity, credential rotation, or shorter default expiry
without an independently reviewed migration. The [wire contract](PROTOCOL.md)
now has a separate implementation boundary for future native clients.

Consider passive network observers, leaked databases/backups, stolen bearer
credentials, a malicious relay/key directory, malicious group members, and
compromised devices or browser JavaScript. HTTPS authenticates the origin, not
the honesty of that origin. Honest client software and an uncompromised device
are necessary for E2EE. Room password admission deliberately trusts the server
and TLS terminator with submitted passwords; these are not message keys.

## Findings and changes in this pass

| Area | Finding and action | Remaining boundary |
| --- | --- | --- |
| Forward secrecy | Keep the legacy static-plus-ephemeral construction; document its limits precisely | A recipient identity-key compromise decrypts recorded inbound traffic: both DH values are recoverable from that key and public headers. Sender-only identity compromise does not by itself recover past outbound ephemeral DH values. No ratchet or PCS exists. |
| Identity | Calculate full fingerprints locally; pin the first peer key with an atomic persistent check; block changed keys before encryption/decryption | Trust on first use is not authentication. Initial substitution, new public IDs, malicious rosters, and malicious served code remain possible. |
| Key storage | Generate new identity and ephemeral private CryptoKeys non-exportable from the outset; remove identity export/reimport | Same-origin script can still invoke keys and read decrypted data; browser/profile compromise and copied storage remain threats. |
| WebSocket sessions | Revalidate the individual socket credential before each push/backlog, on incoming frames, and every 30 seconds while idle | A check cannot retract data already sent or eliminate the small check/send race. Extra database reads are a deliberate cost. |
| Persistence/deletion | Missing history storage fails closed; exact ACK/read bind random delivery IDs and full logical identities; read deletion and receipt creation are atomic | Legacy clients retain ambiguous deletion APIs. Send idempotency and multi-tab local storage coordination remain follow-up work. |
| Metadata | Shared runner disables raw access logs on both listeners; browser polling no longer adds a redundant client timestamp | Proxy/CDN/container logs are separately controlled; sender/recipient IDs, membership, timing, ciphertext sizes, and receipts remain visible to the relay. |

Pins occupy new `peer:` records in the existing IndexedDB store. Existing history
and identity records require no migration. A mismatch leaves the old pin and
history intact. There is deliberately no automatic replacement or destructive
reset flow. Normal device resets produce new participant IDs; verify those again.
For an unexpected change under an existing ID, compare fingerprints out of band
and investigate the server record. Do not clear local history to silence it.
Pinning starts only when the updated client first observes a key; it cannot
retroactively verify previously used keys. Rolling back client code loses pin
enforcement but does not change the ciphertext/history format.

## Session and invite review

Client JWT decoding pins HS256 and requires expiry, subject, type, public ID, and
issuing invite ID. HTTP and WS validate the active user/public ID and invite
existence, explicit revocation, deletion, and expiry. A full/consumed invite is
not itself revoked. Bearer credentials authorize API operations, including
destructive ACK/read operations; they are not proof of possession of a message
private key. Stolen JWTs cannot alone decrypt messages but can fetch or delete
ciphertext and disrupt delivery. Tokens are not bound to a specific device key.

Defaults remain 30 days for client JWTs and 12 hours for admin sessions. Reducing
these now would affect newly issued tokens without providing all existing
clients a refresh path. Invite resume credentials are high-entropy bearer
secrets with SHA-256 digests on the server; they do not rotate or expire
independently of the invite lifecycle. Stateless JWT logout cannot invalidate
an already stolen token. Rotating the signing secret invalidates all sessions.

Next, design an additive session registry with short-lived access tokens,
hashed rotating refresh credentials, replay detection, bounded retry grace for
lost responses, per-device revocation, and explicit legacy-token sunset.
Account for concurrent tabs, offline clients, sealed invites, restored invites,
and lost activation responses before selecting default TTLs. Do not silently
rotate invite retry secrets: a lost response could otherwise strand the only
credential able to resume a sealed room.

Password admission already uses salted scrypt, constant-time verifier checks,
per-invite persisted attempt limits, secure-transport enforcement for passwords,
and serialized admission transactions. Guessing can still lock out an invite;
the expensive password KDF and unlimited unprotected requests need deployment
resource/rate limits. URL invite secrets and local resume credentials must be
treated as credentials. Non-password admission and API transport rely on the
deployment enforcing TLS. No extra request secrets are introduced in this pass.

Admin cookies use HttpOnly and SameSite=Strict; Secure depends on the configured
public URL. Admin sessions remain stateless, without explicit CSRF tokens,
server-side logout revocation, or login throttling. Keep administration private;
SameSite alone is not a complete defense against hostile same-site origins.
These flows need their own tested hardening pass, not changes hidden in an E2EE
migration. `/me` currently reports no access deadline for an unlimited invite,
even though its JWT still expires; expiry enforcement itself uses the JWT.

## Follow-up: exact delivery acknowledgements

`POST /api/v1/ack/exact` and `POST /api/v1/chats/{chat_id}/read/exact`
require full logical identities plus random 128-bit `delivery_id` values. Owner
checks remain mandatory. Polling, WS backlog, and live delivery carry the same
identifier. SQLite row reuse, repeated sender/client IDs, and different group
senders choosing the same client message ID cannot make an old exact reference
delete a new row. New rows receive fresh IDs; existing rows are backfilled at
startup and retain their IDs across restarts and full database restores.

Read consumption uses `DELETE ... RETURNING`; receipt insertion commits in the
same transaction. Concurrent reads and retries after lost responses create at
most one receipt per queued row, and a receipt-write failure restores the
message. Both legacy and exact read paths share this atomic operation. Exact
requests have bounded reference lists and reject unknown fields. The browser
batches up to 100 references, retains failed batches, and does not fall back to
ambiguous legacy APIs when exact support is unavailable.

This is deletion hardening, not send idempotency or proof of decryption. Duplicate
uploads still produce separate deliveries and can produce separate receipts.
Clients still deduplicate history by logical message identity. A stolen JWT can
still delete its owner's rows; malicious relay metadata and served code remain
outside this protection. No permanent deduplication ledger is introduced.
Delivery IDs are retained only with the queued row. Restored backups can replay
previously acknowledged rows, so local history deduplication remains necessary.
See [upgrade notes](UPGRADING.md#exact-delivery-acknowledgements) for rollout,
SQLite requirements, and rollback constraints.

## Local history, metadata, and deployment

Local history stays encrypted under a separate AES key, with storage location
bound as GCM AAD. Non-exportable keys prevent normal export APIs, not access by
malicious code running in the origin. Stored JWTs and invite resume secrets are
accessible to that origin. Clearing site data, origin changes, or resetting a
device can permanently lose private keys and history. A future ratchet does not
protect plaintext already retained in local history on a compromised device.

Read receipts are server-authenticated metadata, not cryptographic proof that a
peer read a message. The relay can delay, suppress, replay, reorder, or fabricate
metadata. Legacy `/read` and row-ID ACK contracts retain ambiguity under ID
collisions/reuse for compatibility. The bundled browser now uses additive exact
endpoints, described above and in the protocol document.

Queued ciphertext and receipts expire after the configured TTL (default 30 days;
cleanup runs hourly) or are deleted on read/ACK. Users, memberships, invite
credentials/verifiers, names, last-seen timestamps, and configuration exports
persist separately. Logical SQLite deletion is not secure erasure: WAL, free
pages, snapshots, backups, and storage media may retain old bytes. No shorter
TTL or automatic metadata purge is introduced because that could destroy offline
delivery or reconnects. Limit backup/export access and retention independently.

The `/client` CSP denies inline/external scripts, framing, object embedding, and
cross-origin connections; user content uses `textContent`. Non-static responses
have no-store and no-referrer headers. CSP cannot defend against a malicious
origin changing its own JavaScript or policy. Prefer independently distributed,
signed native clients for that threat, with key verification independent of the
relay. WebAssembly would not fix the browser delivery trust problem.

Require HTTPS/WSS outside loopback. Restrict trusted proxy addresses; do not trust
arbitrary forwarded headers. Set HSTS at the public TLS terminator after verifying
the deployment. Keep `/admin`, `/docs`, `/redoc`, and `/openapi.json` private for
both HTTP and WebSocket upgrades. The shared runner has a path allowlist and a
shared connection registry; retain that topology. Standalone Uvicorn/Docker and
reverse proxies need explicit access-log suppression/redaction for invite paths,
query strings, Authorization, cookies, WS auth headers/frames, and request bodies.
Uvicorn logs WS URLs at INFO on its error logger independently of HTTP access
logging, so the shared runner also sets Uvicorn's level to warning. Application
logging and Uvicorn warnings/errors remain enabled. Standalone deployments need
both `--no-access-log` and `--log-level warning` (or equivalent redaction). The
runner's change does not configure upstream logs or erase old logs.

The browser continues first-frame WS authentication. Query and legacy subprotocol
authentication remain supported for compatibility; use first-frame auth in new
clients. Cross-origin browser cookies are not accepted as client WS credentials.
An Origin restriction alone cannot stop a native client holding a stolen bearer
token. Gateway connection, frame-size, and rate limits remain necessary for DoS.

## Established protocol/library assessment

These are candidates for a separate prototype, not newly installed dependencies.
Recheck exact releases, advisories, audit scope, licenses, and platform support
when selecting a library. An audited primitive does not audit its composition.

| Candidate | Fit and maturity evidence | Migration cost / decision |
| --- | --- | --- |
| Signal Double Ratchet plus authenticated asynchronous setup | Established specification for per-message key evolution and recovery after fresh uncompromised DH input; use X3DH/PQXDH setup and an existing implementation | Strong candidate for pairwise sessions. Requires signed identity/prekeys, atomic prekey consumption, durable ratchet/skipped-key state, replay limits, and recovery. Not a replacement for one HKDF call. See [Double Ratchet](https://signal.org/docs/specifications/doubleratchet/) and [X3DH](https://signal.org/docs/specifications/x3dh/). |
| libsignal | Rust implementation used by Signal, with Java, Swift, and TypeScript wrappers | Native integration candidate, but upstream says external use is unsupported and APIs may change. Node bindings are not a drop-in browser Web Crypto module; assess AGPL obligations and distribution before adoption. See [upstream](https://github.com/signalapp/libsignal). |
| MLS / OpenMLS | RFC 9420 targets asynchronous groups with forward secrecy and PCS. OpenMLS reports an SRLabs audit; its report includes findings and remediation, not a blanket guarantee | Candidate if groups are central. Requires authenticated credentials, KeyPackages, ordered commits/epochs, welcomes/removals, durable group state, and fork handling. OpenMLS offers WASM builds but lists WASM/mobile targets as built rather than tested in its CI. See [RFC](https://www.rfc-editor.org/rfc/rfc9420.html), [audit announcement](https://blog.openmls.tech/), [platform support](https://github.com/openmls/openmls), and [WASM requirements](https://book.openmls.tech/user_manual/wasm.html). |
| libsodium authenticated boxes / sealed boxes | Established encryption building blocks | Not a session ratchet or PCS solution. Sealed boxes intentionally omit sender authentication. Changing to these formats alone would break v1 interoperability without meeting the goal. See [sealed boxes](https://doc.libsodium.org/public-key_cryptography/sealed_boxes). |

Recommendation: prototype a maintained Signal implementation for pairwise native
sessions, and separately evaluate OpenMLS for group requirements before choosing
one architecture. Do not write a bespoke Double Ratchet or adopt an unreviewed
JavaScript port solely because it runs in the current browser client.

## Migration gates and next work

1. Exact-identity ACK/read support is implemented with tests for delayed ACKs,
   row reuse (including repeated logical IDs), group-ID collisions, lost responses,
   concurrent reads, transaction rollback, and backups. Next add server send
   idempotency with an explicit retention policy, conflict handling, durable
   outgoing retries, and real multi-tab storage tests. Preserve legacy endpoints.
2. Prototype protocols away from live identities. Compare maintenance, licensing,
   mobile/browser support, audit coverage, memory/key storage, offline behavior,
   group membership, and bandwidth. Require cross-language vectors and review.
3. Design versioned, authenticated capability negotiation and bind new identities
   to verified old identities through an authenticated transition or fresh
   out-of-band verification. Never reinterpret a non-exportable legacy X25519 key
   as a signing key. Never silently downgrade a ratcheted peer to v1.
4. Make ratchet-state update, decrypted history, deduplication, and ACK intent
   crash-consistent in one local transaction. Persist outgoing state/envelopes
   before upload so retries reuse the same ciphertext. Bound skipped-key storage
   and define handling for out-of-order messages, offline gaps, resets, and replay.
5. Keep v1 receive support for queued legacy messages and preserve local history.
   Use explicit opt-in sessions/rooms for v2. For mixed groups, require all members
   to agree before upgrading; define membership epochs and leaving-member access.
   Do not dual-encrypt the same new plaintext under v1 and claim ratchet security.
6. Introduce renewable credentials independently. Stage rollout, test rollback
   with isolated backups, then obtain independent security review before making
   v2 or shorter sessions the default. Rollback must not reset ratchet state or
   reuse message keys. Old messages gain no retroactive forward secrecy.

## Verification and limits

Tests were added before changing cryptographic wrappers, identity checks, socket
authorization, and persistence failure behavior. Coverage includes independent
v1 encryption/decryption, context/header/body tampering, non-exportability,
legacy encrypted history, group recipient isolation, concurrent first-use pins,
pin persistence failure, changed-key ACK suppression, and all supported WS auth
transports. HTTP integration tests use isolated temporary databases. The CI test
workflow includes the new JavaScript suites and module syntax check.

Initial-pass local validation: 60 Python tests and 27 JavaScript tests passed. Ruff,
JavaScript syntax checks, `pip check`, `compileall`, whitespace checks, local
documentation links, and landing-page structured data passed. The Python suite
emits an existing Starlette/AnyIO deprecation warning. Landing copy, API and
deployment references, SEO/structured data, and the social preview were reviewed;
the existing backend-focused preview and metadata remain accurate.

Exact-delivery follow-up validation: 73 Python tests and 30 JavaScript tests
passed. New coverage includes identity and owner mismatches, stale message and
receipt references after row reuse, group collisions, concurrent reads, lost
responses, receipt-write rollback, WS/poll reference consistency, migration and
backup stability, bounded browser batches, and refusal to fall back to legacy
deletion. Ruff, JavaScript syntax checks, `pip check`, `compileall`, whitespace,
local documentation links, page assets, and structured data passed. The existing
Starlette/AnyIO warning remains. API/upgrade docs, landing copy and snippets were
updated; SEO metadata and the backend-focused social preview remain accurate.
Browser checks in this pass used the Node harness, not a live browser session.

Live browser verification remains incomplete: initial browser discovery found
no connection, and a later attempt was blocked by a browser integration mismatch.
IndexedDB transaction tests use a serialized adapter; real browser behavior and
native cross-platform interoperability still need release QA. This review does
not include penetration testing, a full dependency audit, load testing, or a
cryptographic proof. No running service, production database, or deployed client
was migrated or restarted.
