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
    assert batch < html.index('id="sync-status"') < html.index('hx-post="/api/sync/trigger"')
    assert html.index('hx-post="/api/sync/trigger"') < canary
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


@pytest.mark.asyncio
async def test_status_swaps_morph_in_place_without_layout_shift(client: httpx.AsyncClient) -> None:
    page = (await client.get("/")).text
    status = (await client.get("/partials/status")).text

    htmx = page.index("htmx.org@2.0.4/dist/htmx.min.js")
    assert htmx < page.index("https://unpkg.com/idiomorph@0.3.0/dist/idiomorph-ext.min.js")
    assert 'hx-ext="morph"' in page and "Idiomorph.defaults" in page
    assert 'hx-swap="morph"' in page and "hx-preserve" not in status
    assert 'id="status-banner" class="min-h-' in status
    assert status.count("min-h-[") >= 9 and "tabular-nums" in status


@pytest.mark.asyncio
async def test_layout_dimensions_are_frozen_between_polls(client: httpx.AsyncClient) -> None:
    page = (await client.get("/")).text
    status = (await client.get("/partials/status")).text

    assert 'class="data-table table-fixed' in status
    widths = ["w-[180px]", "w-[120px]", "w-[200px]", "w-[80px]"]
    colgroup = status[status.index("<colgroup>") + len("<colgroup>") : status.index("</colgroup>")]
    assert [w for w in widths if w in colgroup] == widths and colgroup.count("<col") == 5
    assert status.count("min-h-[96px]") == 6  # 3 state + 3 counter cards (no planned card live)
    assert "time { display: inline-block; min-width: 160px;" in page
    assert "font-variant-numeric: tabular-nums" in page
