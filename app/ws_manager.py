"""Tracks active WebSocket connections per user."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[user_id].add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if sockets is None:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        return bool(self._connections.get(user_id))

    async def send_json(self, user_id: int, payload: dict[str, Any]) -> None:
        sockets = list(self._connections.get(user_id, ()))
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect(user_id, ws)

    async def revoke(self, user_ids: set[int]) -> None:
        """Notify and close every local socket for revoked sessions."""

        for user_id in user_ids:
            sockets = list(self._connections.get(user_id, ()))
            for ws in sockets:
                try:
                    await ws.send_json(
                        {"type": "auth_error", "reason": "invite revoked"}
                    )
                    await ws.close(code=1008)
                except Exception:
                    pass
                finally:
                    await self.disconnect(user_id, ws)


manager = ConnectionManager()
