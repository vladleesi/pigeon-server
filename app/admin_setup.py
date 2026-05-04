"""Bootstrap the first admin user from environment variables."""

from __future__ import annotations

import logging

from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import Admin
from .security import hash_password

logger = logging.getLogger(__name__)


async def ensure_default_admin() -> None:
    settings = get_settings()
    username = settings.admin_username
    password = settings.admin_password
    if not username or not password:
        return

    async with session_scope() as session:
        result = await session.execute(select(Admin).where(Admin.username == username))
        existing = result.scalar_one_or_none()
        if existing is not None:
            return
        admin = Admin(
            username=username,
            password_hash=hash_password(password),
        )
        session.add(admin)
        await session.commit()
        logger.info("created default admin '%s' from environment", username)
