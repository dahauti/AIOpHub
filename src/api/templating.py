"""Shared Jinja environment for the dashboard templates and its display filters."""

from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path

from fastapi.templating import Jinja2Templates

from src.schemas.schedule import WEEKDAYS

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


def format_utc(value: datetime | None) -> str:
    """Render timestamps uniformly in UTC; em dash when absent."""
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_iso_utc(value: datetime) -> str:
    """Machine-readable UTC instant for `<time datetime>`; the browser localises it."""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def http_reason(code: int) -> str:
    """Standard reason phrase for an HTTP status ("" when non-standard)."""
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return ""


TEMPLATES.env.filters["utc"] = format_utc
TEMPLATES.env.filters["iso_utc"] = format_iso_utc
TEMPLATES.env.filters["http_reason"] = http_reason
TEMPLATES.env.globals["weekdays"] = WEEKDAYS
