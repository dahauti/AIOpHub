"""Cockpit layout: controls above a scroll-capped audit zone; one incremental input at a time."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl

AUDIT_FRAME = "max-h-[380px] overflow-y-auto border border-slate-800 rounded-xl bg-slate-900/40"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.state.sync_control = FakeControl(SNAPSHOT)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _wrapper(html: str, mode: str) -> str:
    match = re.search(rf'<span [^>]*data-schedule-mode="{mode}"[^>]*>', html)
    assert match is not None, mode
    return match.group(0)


def _input(html: str, name: str) -> str:
    match = re.search(rf'<input [^>]*name="{name}"[^>]*>', html)
    assert match is not None, name
    return match.group(0)


@pytest.mark.asyncio
async def test_controls_sit_above_the_audit_zone(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text

    order = [
        'id="sync-status"',
        'class="grid grid-cols-1 items-start gap-4 lg:grid-cols-2"',
        'id="schedule-card"',
        'id="single-vehicle-card"',
        'id="audit-heading"',
        f'class="{AUDIT_FRAME}"',
        'id="audit-log"',
    ]
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert "Recent sync activity" in html
    status = html[html.index('id="sync-status"') : html.index("</section>", positions[0])]
    assert 'id="audit-log"' not in status


@pytest.mark.asyncio
async def test_status_swaps_refresh_the_detached_audit_table_out_of_band(
    client: httpx.AsyncClient,
) -> None:
    html = (await client.post("/partials/config/toggle-auto-sync")).text

    assert html.lstrip().startswith('<section id="sync-status"')
    assert html.index("</section>") < html.index('<div id="audit-log" hx-swap-oob="true"')


@pytest.mark.asyncio
async def test_nightly_mode_shows_only_the_time_picker(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text

    assert "hidden" not in _wrapper(html, "nightly").split('class="', 1)[1].split('"')[0]
    assert 'class="flex items-center gap-2 hidden"' in _wrapper(html, "interval")
    assert " required" in _input(html, "nightly_time")
    assert " required" not in _input(html, "interval_minutes")


@pytest.mark.asyncio
async def test_interval_mode_shows_only_the_minutes_input(client: httpx.AsyncClient) -> None:
    form = {
        "schedule_mode": "interval",
        "nightly_time": "02:00",
        "interval_minutes": "30",
        "full_sync_weekday": "6",
        "full_sync_time": "02:00",
    }

    html = (await client.post("/partials/config/schedule", data=form)).text

    assert 'class="flex items-center gap-2 hidden"' in _wrapper(html, "nightly")
    assert "hidden" not in _wrapper(html, "interval").split('class="', 1)[1].split('"')[0]
    assert " required" in _input(html, "interval_minutes")
    assert " required" not in _input(html, "nightly_time")


@pytest.mark.asyncio
async def test_mode_dropdown_toggles_inputs_client_side(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text

    start = html.index('<select name="schedule_mode"')
    handler = html[start : html.index("<option", start)]
    assert 'onchange="' in handler and "data-schedule-mode" in handler
    assert "classList.toggle('hidden'" in handler and ".required" in handler
