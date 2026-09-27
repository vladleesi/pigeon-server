# Changelog

## 0.3.0 — 2026-09-27

- Reject malformed non-ASCII CSRF tokens without a server error and accept validated same-origin no-referrer login forms.
- Keep existing chat messages in place during incoming delivery and preserve the scroll position when reading older messages.
- Stabilize the message viewport and composer during sends/read receipts; show pending retries in the sidebar and prevent input focus from scrolling the page.
- Deduplicate message uploads within a bounded retry window and persist an encrypted browser outbox before sending.
- Add admin CSRF protection, persistent login throttling, and revocable sessions.
- Add opt-in renewable client sessions with hashed refresh rotation, replay detection, per-device revocation, and explicit legacy sunset controls.
- Bound request bodies/rates, WebSocket connections/frames, queued ciphertext, receipts, retry metadata, and participant creation; enforce TLS and local-only administration by default.
- Add exact delivery ACK/read endpoints and random delivery IDs; switch browser retries to the safer contract while preserving legacy APIs.
- Atomically consume read messages and create receipts, preventing duplicate receipts from concurrent readers.
- Preserve the browser v1 wire format while separating protocol operations from UI and storage.
- Compute fingerprints locally, pin peer keys, and block unexpected key changes.
- Generate new private keys non-exportable and retain messages when history storage is unavailable.
- Revalidate WebSocket sessions before delivery and while idle; preserve existing authentication methods.
- Disable raw shared-runner access logs and document security limits and protocol migration requirements.

## 0.2.0 — 2026-09-27

- Standardize Sideword Server configuration, Docker resources, and client identifiers.
- Add a shared backend version for health and OpenAPI responses.
- Establish `develop` for changes and checked promotion to `main`.
- Add protected invites, resumable participant slots, and sealed rooms.
- Improve admin invite creation, encrypted password recovery, and live link lists.

## 0.1.0

- Initial backend: invite-based chats, encrypted-message relay, browser test client, administration, and configuration export/import.
