# Security review and incremental migration plan

Reviewed 2026-09-27. This is a source review with regression tests, not an
independent audit or proof that the application has no vulnerabilities.

## Decision and threat model

Keep the existing v1 encryption protocol, identities, invite retry credentials,
group fanout, delivery rules, and encrypted browser history. Release 0.3.0 adds
renewable sessions with shorter access tokens; the bundled client migrates valid
legacy sessions automatically. Legacy API clients remain compatible until the
operator sets explicit sunset controls. A ratchet or new signing identity still
requires a separately reviewed migration. The [wire contract](PROTOCOL.md)
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
| Persistence/deletion | Missing history storage fails closed; exact ACK/read bind random delivery IDs and full logical identities; read deletion and receipt creation are atomic | Legacy clients retain ambiguous deletion APIs. Send retries now use a bounded ledger and encrypted local outbox; real-browser QA remains unverified. |
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

Renewable sessions are additive: new browser activations opt in, and a browser
with a valid legacy JWT exchanges it for a registered session. Access tokens
last 15 minutes; registered sessions expire after 30 days without extension.
Refresh credentials are stored as SHA-256 digests. Rotation records retain old
digests until session expiry to detect replay. The client persists a proposed
new credential before submitting it; the identical old/new pair may retry for
30 seconds while that successor remains current. Other old-token reuse revokes
the session. HTTP and WS validate the session registry, user, and invite.
Per-device session listing/revocation is available through the API. Browser
Web Locks serialize rotations; ordinary identity saves preserve newer credentials
written by another tab. Long offline periods do not erase retained history.

Legacy JWTs retain their existing expiry unless the operator sets
`SIDEWORD_LEGACY_TOKEN_DEADLINE`. They can still bypass per-device revocation
until sunset; do not call migration complete before disabling them. Existing
invite resume credentials deliberately remain independent and stable, enabling
explicit recovery into a fresh session after revocation. A stolen resume secret
therefore remains an admission credential until the invite is revoked/deleted.
New renewable activations persist their initial session secret before admission,
so a lost response does not strand a sealed room. Sessions are bearer credentials,
not proof of possession of the message private key. Independent review is still
required before declaring the credential migration production-ready.

Admin JWTs now require a live server-side session. Logout and CLI password resets
revoke sessions; old stateless admin JWTs require a fresh login. Admin forms,
including login and multipart import, carry signed cookie/session-bound CSRF
tokens. Unsafe requests check Origin; no-referrer forms with absent/null Origin
require same-origin Fetch Metadata and a valid CSRF token. Clients without Origin
or Fetch Metadata need the CSRF header. Explicit foreign origins are rejected. Cookie-free admin API bearer requests do not use ambient cookie
authority. Cookies are HttpOnly/SameSite=Strict and Secure on HTTPS or a configured
HTTPS public URL. Login limits persist across processes/restarts, use keyed hashes
of IP/account identifiers, and apply before password verification. Lockouts can
still deny service to an account within their one-minute window.

`/me` reports both access-token and renewable-session deadlines, including for
unlimited invites. Invite expiry caps both. Short access-token expiry no longer
causes the browser to discard a renewable session while offline.

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

## Follow-up: delivery integrity and resource limits

Sends are serialized with SQLite write transactions and keyed by chat, sender,
and client message ID. Within the configured 30-day retry window, identical
recipient/ciphertext payloads return the original response without enqueueing
again; conflicting payloads receive 409. Partial group delivery, ACK/read deletion,
and outbox cancellation do not remove retry evidence. The ledger contains hashes,
routing metadata, and timestamps, not plaintext or retained ciphertext. It expires
independently of queued deliveries. Evidence absent from older backups cannot be
reconstructed. Pre-upgrade queued rows without ledger records reject matching
IDs rather than silently creating another fanout; already deleted pre-upgrade
messages have no retry guarantee.

The browser encrypts outgoing plaintext/envelopes locally before upload and
reuses the saved ciphertext across retries and reloads. History commits before
the retry record is removed. Web Locks serialize outbox uploads across tabs.
Expired retries remain visible for manual resolution and are never automatically
resent beyond the ledger window. Pending-send failures do not block polling.
Legacy ACK/read APIs can be retired with `SIDEWORD_ALLOW_LEGACY_ACK=false` after
client migration. Compatibility defaults still permit their original risks.

ASGI guards bound bodies (including chunked requests), body-read time, request
rates, WS connections and inbound frames. Database transactions bound queued
message counts/bytes, receipts, send records and participant creation. Capacity
exhaustion rejects new work without evicting live retry evidence. Poll/backlog
batches are bounded. Login hashing runs off the event loop, and invite admission
holds a database write reservation while verifying scrypt, bounding concurrent
admission KDFs. Distributed traffic and slow peers still require gateway limits;
HTTP/WS rate/connection counters are per process, whereas database quotas and
login limits are shared. Defaults and tuning are in `.env.example` and the API
and upgrade guides. Unauthenticated origin-wide quotas can be exhausted by an
attacker; rate limits bound cost, not availability under every attack.

HTTPS/WSS is required outside a loopback peer plus loopback Host. Administration
and schema routes require that same local boundary by default, in addition to the
shared listener's allowlist. Only explicitly trusted proxy IPs may supply client
address/scheme. Docker/shared runner suppress access logs and bound transport
concurrency/frame sizes. SQL exception logging hides query parameters. Upstream
logging, HSTS, backup encryption/access, and host/container resource limits remain
operator responsibilities; no production environment was modified in this pass.

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
pages, snapshots, backups, and storage media may retain old bytes. No shorter message
TTL or automatic user/invite purge is introduced because that could destroy offline
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

1. Stages 1-4 have code and automated regression coverage: exact delivery, bounded
   send idempotency/outbox, admin CSRF/revocation/throttling, renewable sessions,
   and transport/resource guards. Roll out server-first, validate operator controls,
   migrate clients, then explicitly sunset legacy JWTs and ACKs. Real-browser QA
   is skipped at the user's request; independent security review remains open.
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
6. Renewable credentials are implemented independently. Stage rollout, test rollback
   with isolated backups, then obtain independent security review before making
   v2 or shorter sessions the default. Rollback must not reset ratchet state or
   reuse message keys. Old messages gain no retroactive forward secrecy.

## Verification and limits

Release 0.3.0 preparation reviewed authentication, refresh replay handling, CSRF,
delivery retry/retention, quotas, and changed client persistence paths. Malformed
non-ASCII CSRF values now return 403 instead of raising a comparison error.
Same-origin no-referrer login regression coverage and chat layout fixes are
included. This source review does not replace an independent security audit.

Initial stages 1-4 automated validation: **87 Python tests and 34 JavaScript tests
passed**. Ruff, client/form syntax checks, documentation targets, structured data,
Docker command syntax, whitespace and privacy exclusions passed. Coverage includes
send races, conflicting retries, partial group delivery, retained backup evidence,
queue limits, encrypted outbox recovery, refresh replay/grace, stale-tab writes,
admin CSRF/logout/throttling, private routes, TLS, chunked bodies, and WS limits.
The existing Starlette/AnyIO deprecation warning remains. Browser checks are
skipped at the user's request. The security changes and login fix were deployed to the local backend during
follow-up work. Release 0.3.0 preparation adds the malformed-CSRF regression fix;
that follow-up has not been redeployed. Compatibility sunsets and upstream/backup
operational controls remain rollout tasks.


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

IndexedDB tests use serialized adapters rather than a real browser. Browser
behavior and native cross-platform interoperability still need release QA. This
review does not include penetration testing, a full dependency audit, load testing,
or a cryptographic proof. No running service or production database was migrated
or restarted during the stages 1-4 implementation.
