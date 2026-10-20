"""Dashboard layout: separate batch and canary cards, HTTP badges, truncated error cells."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.api.routes_ui import BLANK_SERIAL
from src.main import create_app
from src.schemas.sync import SingleSyncResult
from tests.ui_fakes import SNAPSHOT, FakeControl, FakeSingleSync

SYNCED = SingleSyncResult(
    serial_no="", outcome="SYNCED", action="SINGLE_UPSERT", status_code=201, dry_run=False
)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.state.sync_control = FakeControl(SNAPSHOT)
    app.state.single_sync = FakeSingleSync(SYNCED)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.mark.asyncio
async def test_batch_and_canary_live_in_separate_cards(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text

    batch, canary = html.index('id="batch-sync"'), html.index('id="single-vehicle"')
    assert batch < html.index('hx-post="/api/sync/trigger"') < html.index('id="sync-status"')
    assert html.index('id="sync-status"') < canary
    assert canary < html.index('hx-post="/api/sync/vehicle"') < html.index('id="single-debug"')
    assert 'hx-target="#single-debug"' in html


@pytest.mark.asyncio
async def test_http_codes_render_as_coloured_badges(client: httpx.AsyncClient) -> None:
    html = (await client.get("/partials/status")).text

    assert '<span class="badge badge-ok">201</span>' in html
    assert '<span class="badge badge-err">422</span>' in html


@pytest.mark.asyncio
async def test_error_cell_is_truncated_with_full_text_on_hover(client: httpx.AsyncClient) -> None:
    status = (await client.get("/partials/status")).text
    page = (await client.get("/")).text

    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert f'<td title="{escaped}"><span class="error-cell">{escaped}</span></td>' in status
    for rule in ("max-width: 300px", "white-space: nowrap", "text-overflow: ellipsis"):
        assert rule in page


@pytest.mark.asyncio
async def test_vehicle_result_renders_in_canary_card_and_refreshes_status(
    client: httpx.AsyncClient,
) -> None:
    html = (await client.post("/api/sync/vehicle", data={"serial_no": "SN-1"})).text

    assert html.lstrip().startswith('<div id="single-debug"')
    assert "Synced vehicle SN-1: HTTP 201" in html
    status = html[html.index('<section id="sync-status"') :]
    assert 'hx-swap-oob="true"' in status.split(">", 1)[0]
    assert "Synced vehicle SN-1" not in status


@pytest.mark.asyncio
async def test_blank_serial_message_stays_in_canary_card(client: httpx.AsyncClient) -> None:
    html = (await client.post("/api/sync/vehicle", data={"serial_no": "  "})).text

    assert html.index(BLANK_SERIAL) < html.index('<section id="sync-status"')
    assert html.lstrip().startswith('<div id="single-debug"')
