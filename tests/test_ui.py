"""HTMX dashboard routes over ASGITransport with a fake SyncControl (no lifespan, no DB)."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.adapters.bc_client import select_connection
from src.config import get_settings
from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl


@pytest_asyncio.fixture
async def control_and_client() -> AsyncIterator[tuple[FakeControl, httpx.AsyncClient]]:
    app = create_app()
    control = FakeControl(SNAPSHOT)
    app.state.sync_control = control
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield control, client


@pytest.mark.asyncio
async def test_dashboard_renders_state_counters_and_audit(
    control_and_client: tuple[FakeControl, httpx.AsyncClient],
) -> None:
    _, client = control_and_client

    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert html.lower().startswith("<!doctype html>")
    assert "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js" in html
    assert "/static/app.css" in html and "bg-[#0B0F17]" in html
    assert 'hx-post="/api/sync/trigger"' in html
    for fragment in ("IDLE", "2026-10-20 12:30:05 UTC", "2026-10-20 12:15:00 UTC", ">12<", ">34<"):
        assert fragment in html
    assert "SN-0001" in html and "SN-0002" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


@pytest.mark.asyncio
async def test_status_partial_is_polling_fragment(
    control_and_client: tuple[FakeControl, httpx.AsyncClient],
) -> None:
    control, client = control_and_client
    control._snapshot = SNAPSHOT.model_copy(update={"status": "RUNNING", "recent": []})

    response = await client.get("/partials/status")

    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert 'hx-get="/partials/status"' in response.text
    assert 'hx-trigger="every 5s"' in response.text
    assert "RUNNING" in response.text
    assert 'id="audit-log"' not in response.text


@pytest.mark.asyncio
async def test_trigger_starts_sync_and_returns_status_partial(
    control_and_client: tuple[FakeControl, httpx.AsyncClient],
) -> None:
    control, client = control_and_client

    response = await client.post("/api/sync/trigger")

    assert response.status_code == 200
    assert control.triggers == 1
    assert "Sync started" in response.text
    assert "dry run" not in response.text.lower()
    assert "LIVE" in response.text
    assert 'id="sync-status"' in response.text


@pytest.mark.asyncio
async def test_trigger_while_running_returns_info_banner_with_200(
    control_and_client: tuple[FakeControl, httpx.AsyncClient],
) -> None:
    control, client = control_and_client
    control.accept = False

    response = await client.post("/api/sync/trigger")

    assert response.status_code == 200
    assert "Sync already in progress" in response.text


@pytest.mark.parametrize(
    ("profile", "url_prefix"),
    [
        ("onprem", "https://bc.company.local:7048/BC/ODataV4/Company("),
        ("saas", "https://api.businesscentral.dynamics.com/v2.0/"),
    ],
)
def test_active_profile_selects_bc_connection(
    profile: str, url_prefix: str, monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    monkeypatch.setenv("BC_ACTIVE_PROFILE", profile)

    assert select_connection(get_settings()).company_url.startswith(url_prefix)


@pytest.mark.parametrize(
    ("accept", "banner"),
    [
        (True, "Sync started (dry run: no changes will be sent to Frontu)"),
        (False, "Sync already in progress (dry run: no changes will be sent to Frontu)"),
    ],
)
@pytest.mark.asyncio
async def test_trigger_banner_and_mode_flag_dry_run(
    accept: bool, banner: str, control_and_client: tuple[FakeControl, httpx.AsyncClient]
) -> None:
    control, client = control_and_client
    control.accept, control.dry_run = accept, True

    response = await client.post("/api/sync/trigger")

    assert response.status_code == 200
    assert banner in response.text
    assert "DRY RUN" in response.text
