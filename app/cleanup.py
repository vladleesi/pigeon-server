"""Background job that deletes stale undelivered ciphertext.

Even buggy clients must not turn the server into a permanent archive.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from .config import get_settings
from .db import session_scope
from .models import PendingMessage, ReadReceipt

logger = logging.getLogger(__name__)

_settings = get_settings()


async def _purge_once() -> None:
    ttl = timedelta(days=_settings.message_ttl_days)
    cutoff = datetime.now(timezone.utc) - ttl
    async with session_scope() as session:
        msg_res = await session.execute(
            delete(PendingMessage).where(PendingMessage.created_at < cutoff)
        )
        # Receipts use the same TTL as pending messages.
        rcpt_res = await session.execute(
            delete(ReadReceipt).where(ReadReceipt.created_at < cutoff)
        )
        await session.commit()
        if msg_res.rowcount or rcpt_res.rowcount:
            logger.info(
                "purged %s stale messages and %s stale read receipts",
                msg_res.rowcount,
                rcpt_res.rowcount,
            )


async def run_periodic_cleanup(interval_seconds: int = 3600) -> None:
    while True:
        try:
            await _purge_once()
        except Exception:
            logger.exception("cleanup iteration failed")
        await asyncio.sleep(interval_seconds)
