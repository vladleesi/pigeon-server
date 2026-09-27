"""Isolated environment defaults established before test-module imports."""

import os
import secrets
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="sideword-pytest-"))

os.environ.setdefault("SIDEWORD_SECRET_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("SIDEWORD_ADMIN_USERNAME", "admin")
os.environ.setdefault("SIDEWORD_ADMIN_PASSWORD", "adminpass")
os.environ.setdefault("SIDEWORD_DB_PATH", str(_TEST_ROOT / "sideword.sqlite3"))
os.environ.setdefault("SIDEWORD_EXPORTS_DIR", str(_TEST_ROOT / "exports"))
