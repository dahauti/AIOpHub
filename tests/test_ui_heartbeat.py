"""Heartbeat: 204 while idle and unchanged; HX-Trigger so listeners refresh on change."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from tests.ui_fakes import NOW, SNAPSHOT, FakeControl

Ports = tuple[FakeControl, httpx.AsyncClient]
CHANGED = "sync-state-changed"
LISTEN = f'hx-trigger="{CHANGED} from:body"'


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    app = create_app()
    control = FakeControl(SNAPSHOT)
    app.state.sync_control = control
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield control, client


def _tag(html: str, pattern: str) -> str:
    match = re.search(rf"<[a-z]+ [^>]*{pattern}[^>]*>", html)
    assert match is not None, pattern
    return match.group(0)


@pytest.mark.asyncio
async def test_idle_and_unchanged_returns_204_without_trigger(ports: Ports) -> None:
    _, client = ports

    params = {"current_last_sync": NOW.isoformat()}
    response = await client.get("/partials/heartbeat", params=params)

    assert response.status_code == 204
    assert response.content == b"" and "HX-Trigger" not in response.headers


@pytest.mark.parametrize("current", [None, "2000-01-01T00:00:00+00:00"])
@pytest.mark.asyncio
async def test_newer_sync_fires_trigger(ports: Ports, current: str | None) -> None:
    _, client = ports
    params = {} if current is None else {"current_last_sync": current}

    response = await client.get("/partials/heartbeat", params=params)

    assert response.status_code == 200 and response.headers["HX-Trigger"] == CHANGED


@pytest.mark.asyncio
async def test_running_fires_trigger_even_when_unchanged(ports: Ports) -> None:
    control, client = ports
    control._snapshot = SNAPSHOT.model_copy(update={"status": "RUNNING"})

    params = {"current_last_sync": NOW.isoformat()}
    response = await client.get("/partials/heartbeat", params=params)

    assert response.headers["HX-Trigger"] == CHANGED


@pytest.mark.asyncio
async def test_kpi_partial_carries_last_sync_marker(ports: Ports) -> None:
    _, client = ports

    response = await client.get("/partials/kpi")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div id="kpi-metrics"')
    marker = _tag(response.text, 'name="current_last_sync"')
    assert 'type="hidden"' in marker and f'value="{NOW.isoformat()}"' in marker


@pytest.mark.asyncio
async def test_dashboard_wires_heartbeat_and_event_refreshes(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    beat = _tag(html, 'hx-get="/partials/heartbeat"')
    assert 'hx-trigger="every 15s"' in beat and 'hx-swap="none"' in beat
    assert "hx-include=\"[name='current_last_sync']\"" in beat
    kpi = _tag(html, 'hx-get="/partials/kpi"')
    assert LISTEN in kpi and 'hx-target="#sync-status"' in kpi
    assert 'hx-swap="morph:innerHTML"' in kpi
    audit = _tag(html, 'hx-target="#audit-log-container"[^>]*' + LISTEN)
    assert 'hx-get="/partials/audit"' in audit and 'hx-include="#audit-filters"' in audit
    assert 'hx-swap="morph:innerHTML"' in audit
    assert html.count('hx-trigger="every 15s"') == 1
