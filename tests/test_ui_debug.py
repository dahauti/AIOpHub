"""Dashboard local-time markup and the single-vehicle debug inspector (fake ports, no DB)."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from src.schemas.sync import SingleSyncDebug, SingleSyncResult
from src.schemas.warranty import ResolvedWarranty
from tests.pipeline_fakes import warranty
from tests.ui_fakes import SNAPSHOT, FakeControl, FakeSingleSync

Ports = tuple[FakeSingleSync, httpx.AsyncClient]
DEBUG = SingleSyncDebug(
    warranties=[
        warranty("0060/00387", start="2026-10-21", formula="1Y", run=1000),
        warranty("0060/00387", code="EXTENDED_<b>X</b>", start="2026-10-21", formula="5Y"),
    ],
    warranty=ResolvedWarranty(
        start_date="2026-10-21",
        end_date="2027-10-21",
        extended_end_date="2031-10-21",
        indicator_limit=1000,
    ),
    payload=FrontuWorkspaceUpsert(
        systemId="0060/00387",
        title="FARID RCV T1S-10",
        client="1738",
        address="Statybininkų g. 3",
        customValues={"site-guid": "Kauno švara, UAB"},
        warrantyEndDate="2027-10-21",
    ),
)
DRY_RUN = SingleSyncResult(
    serial_no="", outcome="DRY_RUN", action="DRY_RUN_SINGLE_UPSERT", dry_run=True, debug=DEBUG
)
DRY_RUN_LABEL = "DRY RUN - No payload sent to Frontu"


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    app = create_app()
    single = FakeSingleSync(DRY_RUN)
    app.state.sync_control = FakeControl(SNAPSHOT)
    app.state.single_sync = single
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield single, client


async def _sync(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/sync/vehicle", data={"serial_no": "0060/00387"})
    assert response.status_code == 200
    return response.text


@pytest.mark.asyncio
async def test_timestamps_render_as_time_tags_with_utc_fallback(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert '<time datetime="2026-10-20T12:30:05Z">2026-10-20 12:30:05 UTC</time>' in html
    assert '<time datetime="2026-10-20T12:15:00Z">2026-10-20 12:15:00 UTC</time>' in html
    assert "Intl.DateTimeFormat" in html and "htmx:afterSettle" in html


@pytest.mark.asyncio
async def test_dashboard_has_empty_debug_container_outside_polled_status(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert '<div id="single-debug"></div>' in html
    assert html.index('id="canary-bar"') < html.index('id="single-debug"')
    assert html.index('id="single-debug"') < html.index('id="sync-status"')
    assert 'id="single-debug"' not in (await client.get("/partials/status")).text


@pytest.mark.asyncio
async def test_dry_run_inspector_shows_bc_rows_resolution_and_payload(ports: Ports) -> None:
    _, client = ports

    html = await _sync(client)

    assert '<div id="single-debug"' in html
    assert "<details open>" in html and DRY_RUN_LABEL in html
    for fragment in ("STANDART_FACTORY", "1Y", "5Y", "1000", "2026-10-21", "Active"):
        assert fragment in html
    assert "EXTENDED_&lt;b&gt;X&lt;/b&gt;" in html
    for fragment in ("2027-10-21", "2031-10-21"):
        assert fragment in html
    assert "&#34;site-guid&#34;: &#34;Kauno švara, UAB&#34;" in html
    assert "Statybininkų g. 3" in html
    assert "&#34;city&#34;" not in html


@pytest.mark.asyncio
async def test_live_inspector_reports_frontu_status(ports: Ports) -> None:
    single, client = ports
    single.result = DRY_RUN.model_copy(
        update={
            "outcome": "SYNCED",
            "action": "SINGLE_UPSERT",
            "dry_run": False,
            "status_code": 201,
        }
    )

    html = await _sync(client)

    assert "LIVE - payload sent to Frontu: HTTP 201" in html
    assert DRY_RUN_LABEL not in html


@pytest.mark.asyncio
async def test_live_failure_inspector_reports_rejection(ports: Ports) -> None:
    single, client = ports
    single.result = DRY_RUN.model_copy(
        update={"outcome": "FAILED", "dry_run": False, "status_code": 422, "error": "rejected"}
    )

    html = await _sync(client)

    assert "LIVE - Frontu push failed: HTTP 422" in html


@pytest.mark.parametrize("outcome", ["NOT_FOUND", "BUSY"])
@pytest.mark.asyncio
async def test_no_inspector_without_debug_data(ports: Ports, outcome: str) -> None:
    single, client = ports
    single.result = SingleSyncResult.model_validate(
        {"serial_no": "", "outcome": outcome, "dry_run": True}
    )

    html = await _sync(client)

    assert '<div id="single-debug"' in html
    assert "<details" not in html
