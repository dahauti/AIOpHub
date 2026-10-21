"""Next-sync indicator on the Last sync KPI, and the Processed quick filter matching its KPI."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from src.schemas.audit_filter import ACTION_GROUPS, AuditQuery
from src.schemas.sync import AuditRecord
from tests.ui_fakes import NOW, SNAPSHOT, FakeControl

Ports = tuple[FakeControl, httpx.AsyncClient]
NEXT = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    rows = [
        AuditRecord(created_at=NOW, entity_key=f"SN-{action}", action=action)
        for action in ("UPSERT", "ARCHIVE", "SINGLE_UPSERT", "SINGLE_ARCHIVE", "SKIPPED")
    ]
    control = FakeControl(SNAPSHOT.model_copy(update={"recent": rows}))
    app = create_app()
    app.state.sync_control = control
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield control, client


def _next_sync(html: str) -> str:
    start = html.index('<p id="next-sync"')
    return html[start : html.index("</p>", start)]


@pytest.mark.asyncio
async def test_next_sync_shows_paused_while_auto_sync_is_off(ports: Ports) -> None:
    control, client = ports
    control.next_run = NEXT

    html = (await client.get("/")).text

    assert "Next sync:" in _next_sync(html) and ">Paused</span>" in _next_sync(html)
    assert html.index("Last sync") < html.index('id="next-sync"')


@pytest.mark.asyncio
async def test_next_sync_renders_a_localisable_time_when_enabled(ports: Ports) -> None:
    control, client = ports
    control.auto_sync, control.next_run = True, NEXT

    html = (await client.post("/partials/config/toggle-dry-run")).text

    indicator = _next_sync(html)
    assert '<time datetime="2026-10-21T02:00:00Z">2026-10-21 02:00:00 UTC</time>' in indicator
    assert "Paused" not in indicator


def test_processed_group_is_exactly_the_kpi_actions() -> None:
    assert AuditQuery(action="PROCESSED").actions == ACTION_GROUPS["PROCESSED"]
    assert set(ACTION_GROUPS["PROCESSED"]) == {
        "UPSERT",
        "ARCHIVE",
        "SINGLE_UPSERT",
        "SINGLE_ARCHIVE",
    }


@pytest.mark.asyncio
async def test_processed_filter_lists_upserts_and_archives(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/partials/audit", params={"action": "PROCESSED"})).text

    for action in ("UPSERT", "ARCHIVE", "SINGLE_UPSERT", "SINGLE_ARCHIVE"):
        assert f'title="SN-{action}"' in html
    assert 'title="SN-SKIPPED"' not in html and "4 rows" in html
    assert '<option value="PROCESSED" selected>Processed</option>' in html
