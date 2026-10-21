"""Dashboard schedule card: rendering, saving, and validation errors."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl

Ports = tuple[FakeControl, httpx.AsyncClient]
FORM = {
    "schedule_mode": "interval",
    "nightly_time": "02:00",
    "interval_minutes": "30",
    "full_sync_enabled": "true",
    "full_sync_weekday": "0",
    "full_sync_time": "03:15",
}


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    app = create_app()
    control = FakeControl(SNAPSHOT)
    app.state.sync_control = control
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield control, client


@pytest.mark.asyncio
async def test_dashboard_renders_schedule_card_with_stored_values(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert 'id="schedule-card"' in html and 'hx-post="/partials/config/schedule"' in html
    assert '<option value="nightly" selected>' in html
    assert '<option value="6" selected>Sunday</option>' in html
    assert 'name="full_sync_enabled" value="true" role="switch" class="switch-input">' in html
    assert "paused while Auto-sync is OFF" in html


@pytest.mark.asyncio
async def test_saving_schedule_stores_typed_values_and_rerenders_card(ports: Ports) -> None:
    control, client = ports

    response = await client.post("/partials/config/schedule", data=FORM)

    assert response.status_code == 200
    assert control.schedule.key == ("interval", "02:00", 30, True, 0, "03:15")
    html = response.text
    assert html.lstrip().startswith('<div id="schedule-card"')
    assert "Schedule saved: Incremental sync every 30 min; full sync Monday at 03:15" in html
    assert '<option value="interval" selected>' in html
    assert 'class="switch-input" checked>' in html


@pytest.mark.asyncio
async def test_unchecked_switch_turns_full_sync_off(ports: Ports) -> None:
    control, client = ports
    form = {k: v for k, v in FORM.items() if k != "full_sync_enabled"}

    response = await client.post("/partials/config/schedule", data=form)

    assert response.status_code == 200 and control.schedule.full_sync_enabled is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("nightly_time", "25:00"), ("interval_minutes", "1"), ("schedule_mode", "hourly")],
)
@pytest.mark.asyncio
async def test_invalid_schedule_is_rejected_with_422_and_nothing_stored(
    ports: Ports, field: str, value: str
) -> None:
    control, client = ports

    response = await client.post("/partials/config/schedule", data=FORM | {field: value})

    assert response.status_code == 422
    assert f"Invalid schedule: {field}:" in response.text
    assert control.schedule.key == ("nightly", "02:00", 60, False, 6, "02:00")
