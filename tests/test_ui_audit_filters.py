"""Audit log filters: KPI quick-filter triggers, toolbar, server-side query and error detail."""

import re
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio

from src.api.audit_handlers import parse_audit_query
from src.api.templating import http_reason
from src.main import create_app
from src.pipelines.scheduler import SyncRunner
from src.schemas.audit_filter import AuditQuery
from src.schemas.sync import AuditRecord
from tests.pipeline_fakes import NOW as RUN_NOW
from tests.repository_fakes import FakeConfigStore, FakeSyncRepository
from tests.ui_fakes import NOW, SNAPSHOT, FakeControl

LONG_ERROR = "Frontu rejected the payload: " + "field client is unknown; " * 12
TARGET = 'hx-target="#audit-log-container"'
Ports = tuple[FakeControl, httpx.AsyncClient]


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    extra = [
        AuditRecord(
            created_at=NOW,
            entity_key="SN-9",
            action="FAILED",
            status_code=503,
            error_message=LONG_ERROR,
        ),
        AuditRecord(created_at=NOW, entity_key="SN-0003", action="SKIPPED"),
        AuditRecord(created_at=NOW, entity_key="SN-0004", action="SINGLE_UPSERT", status_code=200),
    ]
    control = FakeControl(SNAPSHOT.model_copy(update={"recent": [*SNAPSHOT.recent, *extra]}))
    app = create_app()
    app.state.sync_control = control
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield control, client


def _button(html: str, href: str) -> str:
    match = re.search(rf'<button [^>]*hx-get="{re.escape(href)}"[^>]*>', html)
    assert match is not None, href
    return match.group(0)


def _vehicles(html: str) -> list[str]:
    return re.findall(
        r'<td class="truncate" title="([^"]+)">\1</td>\s*<td class="truncate" title="[A-Z_]+"', html
    )


@pytest.mark.parametrize(
    "action", ["UPSERT", "SKIPPED", "FAILED"], ids=["processed", "skipped", "errors"]
)
@pytest.mark.asyncio
async def test_kpi_cards_are_quick_filter_buttons(ports: Ports, action: str) -> None:
    _, client = ports
    html = (await client.get("/")).text

    button = _button(html, f"/partials/audit?action={action}&amp;range=today")
    assert TARGET in button and "morph:innerHTML" in button and "kpi-action" in button
    assert '<div id="audit-log-container" class="max-h-[380px]' in html


@pytest.mark.asyncio
async def test_toolbar_sits_above_the_table_with_search_action_range_and_reset(
    ports: Ports,
) -> None:
    _, client = ports
    html = (await client.get("/")).text

    toolbar = html[
        html.index('<form id="audit-filters"') : html.index("</form>", html.index("audit-filters"))
    ]
    assert html.index('id="audit-filters"') < html.index('<table class="data-table')
    assert (
        'hx-trigger="keyup changed delay:300ms, search"' in toolbar and 'name="search"' in toolbar
    )
    for label in ("All", "Upsert", "Skipped", "Failed", "Today", "Last 7 Days", "All Time"):
        assert f">{label}</option>" in toolbar
    assert '<option value="all" selected>All Time</option>' in toolbar  # latest rows, any day
    assert ">Reset</button>" in toolbar and 'hx-get="/partials/audit?range=all"' in toolbar


@pytest.mark.asyncio
async def test_action_filter_returns_matching_group_and_reflects_it_in_toolbar(
    ports: Ports,
) -> None:
    control, client = ports

    html = (await client.get("/partials/audit", params={"action": "UPSERT", "range": "7d"})).text

    assert _vehicles(html) == ["SN-0001", "SN-0004"]
    assert control.audit_queries[-1] == AuditQuery(action="UPSERT", date_range="7d")
    assert '<option value="UPSERT" selected>Upsert</option>' in html
    assert '<option value="7d" selected>Last 7 Days</option>' in html
    assert 'class="error-cell"' in html and 'class="error-full"' not in html


@pytest.mark.asyncio
async def test_failed_filter_shows_full_error_payload_and_http_reason(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/partials/audit", params={"action": "FAILED"})).text

    assert _vehicles(html) == ["SN-0002", "SN-9"]
    assert f'<span class="error-full">{LONG_ERROR}</span>' in html
    assert "Service Unavailable" in html and "Unprocessable" in html
    assert 'class="error-cell"' not in html


@pytest.mark.asyncio
async def test_search_filters_by_serial_and_is_kept_in_the_input(ports: Ports) -> None:
    control, client = ports

    html = (await client.get("/partials/audit", params={"search": " sn-000 ", "action": ""})).text

    assert _vehicles(html) == ["SN-0001", "SN-0002", "SN-0003", "SN-0004"]
    assert control.audit_queries[-1].search == "sn-000"
    assert 'name="search" value="sn-000"' in html and "4 rows" in html


@pytest.mark.parametrize(("field", "value"), [("action", "DROP"), ("range", "1y")])
@pytest.mark.asyncio
async def test_unknown_filter_values_are_rejected_without_querying(
    ports: Ports, field: str, value: str
) -> None:
    control, client = ports

    response = await client.get("/partials/audit", params={field: value})

    assert response.status_code == 422 and "Invalid filter" in response.text
    assert control.audit_queries == []


def test_blank_values_mean_no_filter_and_groups_cover_single_vehicle_writes() -> None:
    query = parse_audit_query("", "   ", "all")

    assert (query.action, query.search, query.date_range, query.limit) == (None, None, "all", 50)
    assert AuditQuery(action="UPSERT").actions == ("UPSERT", "SINGLE_UPSERT")
    assert AuditQuery(action="ALL_ERRORS").detailed and not AuditQuery(action="SKIPPED").detailed
    assert (http_reason(404), http_reason(299)) == ("Not Found", "")


@pytest.mark.parametrize(
    ("date_range", "since"),
    [("today", RUN_NOW.replace(hour=0)), ("7d", RUN_NOW - timedelta(days=7)), ("all", None)],
)
@pytest.mark.asyncio
async def test_runner_resolves_date_range_to_a_lower_bound(date_range: str, since: object) -> None:
    repo = FakeSyncRepository(cursor=RUN_NOW)
    runner = SyncRunner(_NoPipeline(), repo, FakeConfigStore(), clock=lambda: RUN_NOW)

    await runner.audit(AuditQuery.model_validate({"date_range": date_range}))

    assert repo.audit_queries[-1][1] == since


class _NoPipeline:
    is_running = False

    async def run(self, *, full: bool = False) -> None:
        return None
