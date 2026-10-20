"""Dashboard cursor form handling: parse and validate a manual cursor (no I/O)."""

from datetime import UTC, datetime

CURSOR_LOCKED = "Sync is running: the cursor cannot be changed until it finishes"
MAX_CURSOR_INPUT = 64


class CursorInputError(ValueError):
    """User-facing reason a submitted cursor was rejected."""


def parse_cursor(raw: str, now: datetime) -> datetime:
    """ISO 8601 (with or without offset) or `datetime-local` text -> aware UTC datetime.

    The dashboard converts `datetime-local` values to UTC in the browser before sending;
    a value that still arrives without an offset is taken as UTC.
    """
    value = raw.strip()
    if not value:
        raise CursorInputError("Enter a cursor date and time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CursorInputError(f"Unrecognised cursor time: {value}") from exc
    cursor = (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    if cursor > now:
        raise CursorInputError("The cursor cannot be in the future")
    return cursor
