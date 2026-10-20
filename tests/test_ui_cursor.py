"""Dashboard cursor controls: reset-to-now, explicit set, RUNNING lock and input validation."""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from src.api.cursor_handlers import CURSOR_LOCKED
from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl

Ports = tuple[FakeControl, httpx.AsyncClient]


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    app = create_app()
    control = FakeControl(SNAPSHOT)
    app.state.sync_control = control
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield control, client


@pytest.mark.asyncio
async def test_reset_now_sets_current_utc_time_and_returns_status(ports: Ports) -> None:
    control, client = ports
    before = datetime.now(UTC)

    response = await client.post("/partials/cursor/reset-now")

    assert response.status_code == 200
    assert '<section id="sync-status"' in response.text and "Cursor set to" in response.text
    [cursor] = control.cursor_sets
    assert cursor.tzinfo is not None and before <= cursor <= datetime.now(UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-01T08:30:00Z", datetime(2026, 10, 1, 8, 30, tzinfo=UTC)),
        ("2026-10-01T11:30:00+03:00", datetime(2026, 10, 1, 8, 30, tzinfo=UTC)),
        ("2026-10-01T08:30", datetime(2026, 10, 1, 8, 30, tzinfo=UTC)),
        ("2026-10-01T08:30:15", datetime(2026, 10, 1, 8, 30, 15, tzinfo=UTC)),
    ],
    ids=["iso-utc", "iso-offset", "datetime-local", "datetime-local-seconds"],
)
@pytest.mark.asyncio
async def test_set_accepts_iso_and_datetime_local(
    ports: Ports, raw: str, expected: datetime
) -> None:
    control, client = ports

    response = await client.post("/partials/cursor/set", data={"cursor_timestamp": raw})

    assert response.status_code == 200
    assert control.cursor_sets == [expected]
    assert "Cursor set to 2026-10-01 08:30" in response.text


@pytest.mark.parametrize("endpoint", ["/partials/cursor/set", "/partials/cursor/reset-now"])
@pytest.mark.asyncio
async def test_cursor_change_is_rejected_with_409_while_running(
    ports: Ports, endpoint: str
) -> None:
    control, client = ports
    control.cursor_locked = True

    response = await client.post(endpoint, data={"cursor_timestamp": "2026-10-01T08:30:00Z"})

    assert response.status_code == 409
    assert CURSOR_LOCKED in response.text and '<section id="sync-status"' in response.text
    assert control.cursor_sets == []


@pytest.mark.parametrize(
    "raw",
    ["", "not-a-date", (datetime.now(UTC) + timedelta(days=1)).isoformat()],
    ids=["empty", "garbage", "future"],
)
@pytest.mark.asyncio
async def test_invalid_cursor_input_is_rejected_with_422(ports: Ports, raw: str) -> None:
    control, client = ports

    response = await client.post("/partials/cursor/set", data={"cursor_timestamp": raw})

    assert response.status_code == 422
    assert '<section id="sync-status"' in response.text
    assert control.cursor_sets == []


def _cursor_controls(html: str) -> list[str]:
    reset = re.search(r'<button[^>]*hx-post="/partials/cursor/reset-now"[^>]*>', html)
    cursor_input = re.search(r'<input[^>]*name="cursor_timestamp"[^>]*>', html)
    submit = re.search(r'<form[^>]*hx-post="/partials/cursor/set".*?(<button[^>]*>)', html, re.S)
    assert reset and cursor_input and submit
    return [reset.group(0), cursor_input.group(0), submit.group(1)]


@pytest.mark.parametrize(("status", "disabled"), [("IDLE", False), ("RUNNING", True)])
@pytest.mark.asyncio
async def test_cursor_controls_are_disabled_while_running(
    ports: Ports, status: str, disabled: bool
) -> None:
    control, client = ports
    control._snapshot = SNAPSHOT.model_copy(update={"status": status})

    html = (await client.get("/partials/status")).text

    controls = _cursor_controls(html)
    assert all((" disabled" in tag) is disabled for tag in controls)
    assert 'type="datetime-local"' in controls[1] and 'hx-target="#sync-status"' in html


@pytest.mark.asyncio
async def test_page_lets_htmx_swap_409_and_422_responses(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert 'name="htmx-config"' in html and "409|422" in html
    assert "https://cdn.tailwindcss.com" in html and "JetBrains+Mono" in html
