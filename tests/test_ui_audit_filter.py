"""Actionable "Errors today" KPI, the failed-only audit view, and the inline autosaving schedule."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.api.routes_ui import http_reason
from src.main import create_app
from src.schemas.sync import AuditRecord
from tests.ui_fakes import NOW, SNAPSHOT, FakeControl

LONG_ERROR = "Frontu rejected the payload: " + "field 'client' is unknown; " * 12
CLEAR = 'hx-get="/partials/audit"'


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    failure = AuditRecord(
        created_at=NOW,
        entity_key="SN-9",
        action="FAILED",
        status_code=503,
        error_message=LONG_ERROR,
    )
    app = create_app()
    app.state.sync_control = FakeControl(
        SNAPSHOT.model_copy(update={"recent": [*SNAPSHOT.recent, failure]})
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _tag(html: str, pattern: str) -> str:
    match = re.search(pattern, html)
    assert match is not None, pattern
    return match.group(0)


@pytest.mark.asyncio
async def test_errors_kpi_filters_the_audit_table_to_failures(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text

    kpi = _tag(html, r'<button [^>]*hx-get="/partials/audit\?status=FAILED"[^>]*>')
    assert 'hx-target="#audit-log-table"' in kpi and "kpi-action" in kpi
    assert '<div id="audit-log-table" class="max-h-[380px]' in html
    assert CLEAR not in html.replace(kpi, "")


@pytest.mark.asyncio
async def test_failed_view_shows_only_failures_with_full_errors_and_reason(
    client: httpx.AsyncClient,
) -> None:
    html = (await client.get("/partials/audit", params={"status": "FAILED"})).text

    assert "SN-0001" not in html and "SN-0002" in html and "SN-9" in html
    assert "Failed today: 2" in html
    assert f'<span class="error-full">{LONG_ERROR.replace(chr(39), "&#39;")}</span>' in html
    assert "Service Unavailable" in html and "Unprocessable" in html
    assert 'class="error-cell"' not in html
    assert CLEAR in html and "Clear filter / Show all" in html


@pytest.mark.asyncio
async def test_unfiltered_view_lists_all_recent_rows_without_clear_button(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/partials/audit")

    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="audit-log"')
    assert "SN-0001" in response.text and "Clear filter" not in response.text


@pytest.mark.asyncio
async def test_unknown_audit_filter_is_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/partials/audit", params={"status": "UPSERT"})).status_code == 422


def test_http_reason_names_standard_codes_only() -> None:
    assert (http_reason(404), http_reason(299)) == ("Not Found", "")


@pytest.mark.asyncio
async def test_schedule_autosaves_on_change_and_keeps_weekly_inputs_on_one_line(
    client: httpx.AsyncClient,
) -> None:
    html = (await client.get("/")).text

    form = _tag(html, r'<form hx-post="/partials/config/schedule"[^>]*>')
    assert 'hx-trigger="change, submit"' in form
    weekly = html[html.index("Weekly full sync</legend>") :]
    row = _tag(weekly, r'<div class="[^"]*">')
    assert row == '<div class="mt-2 flex items-center gap-2">'
    line = weekly[: weekly.index("</div>")]
    assert all(
        name in line for name in ("full_sync_enabled", "full_sync_weekday", "full_sync_time")
    )
