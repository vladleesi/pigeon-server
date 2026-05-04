"""CLI: create or rotate an administrator password.

Usage::

    python -m scripts.create_admin --username admin --password ...

Interactive password prompt when ``--password`` is omitted.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.db import init_db, session_scope
from app.models import Admin
from app.security import hash_password


async def _run(username: str, password: str) -> int:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(Admin).where(Admin.username == username))
        admin = result.scalar_one_or_none()
        if admin is None:
            admin = Admin(username=username, password_hash=hash_password(password))
            session.add(admin)
            print(f"created admin '{username}'")
        else:
            admin.password_hash = hash_password(password)
            print(f"updated password for admin '{username}'")
        await session.commit()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update a Pigeon admin account.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=None, help="omit to prompt securely")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if not password:
        print("empty password rejected", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.username, password))


if __name__ == "__main__":
    raise SystemExit(main())
