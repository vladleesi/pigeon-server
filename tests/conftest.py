"""Isolated environment defaults established before test-module imports."""

import os
import secrets
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="pigeon-pytest-"))

os.environ.setdefault("PIGEON_SECRET_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("PIGEON_ADMIN_USERNAME", "admin")
os.environ.setdefault("PIGEON_ADMIN_PASSWORD", "adminpass")
os.environ.setdefault("PIGEON_DB_PATH", str(_TEST_ROOT / "pigeon.sqlite3"))
os.environ.setdefault("PIGEON_EXPORTS_DIR", str(_TEST_ROOT / "exports"))
