"""POST /api/sync/vehicle and the dashboard's single-vehicle form (fake ports, no DB)."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from src.schemas.sync import SingleSyncResult
from tests.ui_fakes import SNAPSHOT, FakeControl, FakeSingleSync

Ports = tuple[FakeSingleSync, httpx.AsyncClient]
SYNCED = SingleSyncResult(
    serial_no="", outcome="SYNCED", action="SINGLE_UPSERT", status_code=200, dry_run=False
)


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    app = create_app()
    single = FakeSingleSync(SYNCED)
    app.state.sync_control = FakeControl(SNAPSHOT)
    app.state.single_sync = single
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield single, client


@pytest.mark.asyncio
async def test_dashboard_form_posts_serial_with_explicit_force_live(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert 'hx-post="/api/sync/vehicle"' in html
    assert 'name="serial_no"' in html and 'maxlength="128"' in html
    assert '<input type="hidden" name="force_live" value="true">' in html
    assert "Sync Vehicle (Live Single)" in html


@pytest.mark.asyncio
async def test_live_button_payload_syncs_and_reports_http_status(ports: Ports) -> None:
    single, client = ports

    response = await client.post(
        "/api/sync/vehicle", data={"serial_no": "  VSN123 ", "force_live": "true"}
    )

    assert response.status_code == 200
    assert single.calls == [("VSN123", True)]
    assert "Synced vehicle VSN123: HTTP 200" in response.text
    assert 'id="sync-status"' in response.text


@pytest.mark.asyncio
async def test_programmatic_default_is_not_live(ports: Ports) -> None:
    single, client = ports

    await client.post("/api/sync/vehicle", data={"serial_no": "VSN123"})

    assert single.calls == [("VSN123", False)]


@pytest.mark.parametrize(
    ("result", "banner"),
    [
        (
            SYNCED.model_copy(update={"action": "SINGLE_ARCHIVE", "status_code": 201}),
            "Synced vehicle VSN9 (archived): HTTP 201",
        ),
        (
            SingleSyncResult(serial_no="", outcome="DRY_RUN", dry_run=True),
            "Dry run for VSN9: no changes sent to Frontu",
        ),
        (
            SingleSyncResult(serial_no="", outcome="NOT_FOUND", dry_run=False),
            "Vehicle VSN9 not found in BC",
        ),
        (
            SingleSyncResult(serial_no="", outcome="BUSY", dry_run=False),
            "Sync already in progress. Please wait for the current batch to complete.",
        ),
        (
            SingleSyncResult(serial_no="", outcome="FAILED", dry_run=False, error="<b>boom</b>"),
            "Sync of VSN9 failed: &lt;b&gt;boom&lt;/b&gt;",
        ),
    ],
    ids=["archived", "dry-run", "not-found", "busy", "failed-escaped"],
)
@pytest.mark.asyncio
async def test_outcome_banners(ports: Ports, result: SingleSyncResult, banner: str) -> None:
    single, client = ports
    single.result = result

    response = await client.post("/api/sync/vehicle", data={"serial_no": "VSN9"})

    assert response.status_code == 200
    assert banner in response.text


@pytest.mark.asyncio
async def test_blank_serial_is_rejected_without_calling_sync(ports: Ports) -> None:
    single, client = ports

    response = await client.post("/api/sync/vehicle", data={"serial_no": "   "})

    assert response.status_code == 200
    assert "Enter a vehicle SerialNo" in response.text
    assert single.calls == []
