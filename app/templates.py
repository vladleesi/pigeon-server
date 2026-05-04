"""Jinja2 template engine wiring."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _format_dt(value):
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M UTC")


templates.env.filters["fmt_dt"] = _format_dt
