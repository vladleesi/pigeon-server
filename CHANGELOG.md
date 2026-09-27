# Changelog

## Unreleased

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

- Initial backend: invite-based chats, encrypted-message relay, browser test
  client, administration, and configuration export/import.
