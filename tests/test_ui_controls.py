"""Dashboard runtime controls: dry-run / auto-sync toggles and the Sync now / Abort action bar."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.api.config_handlers import MODE_LOCKED, NOTHING_TO_ABORT, SYNC_ABORTED
from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl

Ports = tuple[FakeControl, httpx.AsyncClient]
DRY_TOGGLE = 'hx-post="/partials/config/toggle-dry-run"'
AUTO_TOGGLE = 'hx-post="/partials/config/toggle-auto-sync"'
ABORT = 'hx-post="/partials/sync/abort"'
TRIGGER = 'hx-post="/api/sync/trigger"'


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


def _tag(html: str, marker: str) -> str:
    start = html.rindex("<button", 0, html.index(marker))
    return html[start : html.index(">", html.index(marker)) + 1]


@pytest.mark.asyncio
async def test_toggle_dry_run_switches_badge_from_amber_dry_run_to_emerald_live(
    ports: Ports,
) -> None:
    control, client = ports
    control.dry_run = True
    page = (await client.get("/")).text
    assert "toggle-amber" in _tag(page, DRY_TOGGLE) and "hx-confirm" in _tag(page, DRY_TOGGLE)

    response = await client.post("/partials/config/toggle-dry-run")

    assert response.status_code == 200 and control.dry_run is False
    html = response.text
    assert html.lstrip().startswith('<section id="sync-status"')
    assert 'hx-target="#sync-status"' in _tag(html, DRY_TOGGLE)
    assert "toggle-emerald" in _tag(html, DRY_TOGGLE) and ">LIVE</button>" in html
    assert "Mode set to LIVE" in html


@pytest.mark.asyncio
async def test_toggle_dry_run_is_refused_while_running(ports: Ports) -> None:
    control, client = ports
    control.mode_locked = True
    _running(control)

    response = await client.post("/partials/config/toggle-dry-run")

    assert response.status_code == 409 and MODE_LOCKED in response.text
    assert " disabled" in _tag(response.text, DRY_TOGGLE)


@pytest.mark.asyncio
async def test_toggle_auto_sync_flips_switch(ports: Ports) -> None:
    control, client = ports
    assert "Auto-sync OFF" in (await client.get("/")).text

    response = await client.post("/partials/config/toggle-auto-sync")

    assert response.status_code == 200 and control.auto_sync is True
    assert "Auto-sync enabled" in response.text and "Auto-sync ON" in response.text
    assert "toggle-emerald" in _tag(response.text, AUTO_TOGGLE)


@pytest.mark.asyncio
async def test_idle_action_bar_shows_sync_now_only(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert TRIGGER in html and ABORT not in html


@pytest.mark.asyncio
async def test_running_action_bar_and_poll_show_red_abort_button(ports: Ports) -> None:
    control, client = ports
    _running(control)

    for html in ((await client.get("/")).text, (await client.get("/partials/status")).text):
        assert ABORT in html and TRIGGER not in html
        assert "btn-danger" in _tag(html, ABORT) and "Abort Sync" in html


@pytest.mark.asyncio
async def test_abort_releases_running_sync_and_returns_status(ports: Ports) -> None:
    control, client = ports
    _running(control)

    response = await client.post("/partials/sync/abort")

    assert response.status_code == 200 and control.aborts == 1
    assert SYNC_ABORTED in response.text and TRIGGER in response.text
    assert 'hx-trigger="every 5s"' not in response.text


@pytest.mark.asyncio
async def test_abort_without_running_sync_is_a_conflict(ports: Ports) -> None:
    control, client = ports

    response = await client.post("/partials/sync/abort")

    assert response.status_code == 409 and NOTHING_TO_ABORT in response.text
    assert control.aborts == 0
