"""Status polling: only the KPI block polls, only while RUNNING; the audit table refreshes once."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.api.routes_ui import STOP_POLLING
from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl

Ports = tuple[FakeControl, httpx.AsyncClient]
POLL = 'hx-trigger="every 5s"'


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    app = create_app()
    control = FakeControl(SNAPSHOT)
    app.state.sync_control = control
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield control, client


def _running(control: FakeControl) -> None:
    control._snapshot = SNAPSHOT.model_copy(update={"status": "RUNNING"})


def _opening_tag(html: str, element_id: str) -> str:
    match = re.search(rf'<[a-z]+ id="{element_id}"[^>]*>', html)
    assert match is not None, element_id
    return match.group(0)


@pytest.mark.asyncio
async def test_idle_dashboard_does_not_poll(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert POLL not in html and 'hx-get="/partials/status"' not in html
    assert 'id="kpi-metrics"' in html and 'id="audit-log"' in html


@pytest.mark.asyncio
async def test_running_dashboard_polls_kpis_only(ports: Ports) -> None:
    control, client = ports
    _running(control)

    html = (await client.get("/")).text

    kpi = _opening_tag(html, "kpi-metrics")
    assert POLL in kpi and 'hx-get="/partials/status"' in kpi and 'hx-swap="morph"' in kpi
    assert html.count(POLL) == 1
    assert POLL not in _opening_tag(html, "sync-status") + _opening_tag(html, "audit-log")
    assert html.index('id="kpi-metrics"') < html.index('id="audit-log"')


@pytest.mark.asyncio
async def test_poll_while_running_returns_kpis_without_audit_table(ports: Ports) -> None:
    control, client = ports
    _running(control)

    response = await client.get("/partials/status")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div id="kpi-metrics"')
    assert POLL in response.text and 'id="audit-log"' not in response.text


@pytest.mark.asyncio
async def test_final_poll_stops_polling_and_refreshes_audit_table_once(ports: Ports) -> None:
    _, client = ports

    response = await client.get("/partials/status")

    assert response.status_code == STOP_POLLING == 286
    assert POLL not in response.text
    assert '<div id="audit-log" hx-swap-oob="true"' in response.text
    assert "SN-0001" in response.text


@pytest.mark.parametrize("accept", [True, False])
@pytest.mark.asyncio
async def test_sync_now_starts_polling_even_before_the_run_claims_the_lock(
    ports: Ports, accept: bool
) -> None:
    control, client = ports
    control.accept = accept

    html = (await client.post("/api/sync/trigger")).text

    assert (POLL in _opening_tag(html, "kpi-metrics")) is accept
    assert 'id="audit-log"' in html


@pytest.mark.asyncio
async def test_sync_now_button_swaps_fresh_nodes_so_polling_attaches(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    button = re.search(r'<button[^>]*hx-post="/api/sync/trigger"[^>]*>', html)
    assert button is not None and 'hx-swap="outerHTML"' in button.group(0)
