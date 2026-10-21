"""Task sync cockpit card: render, background trigger, dry-run toggle, polling and audit badges."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

from src.api.routes_ui import STOP_POLLING, SYNC_STATE_CHANGED
from src.api.task_handlers import TASK_MODE_LOCKED, TASKS_ALREADY_RUNNING, TASKS_STARTED
from src.config import get_settings
from src.database import get_engine, init_db
from src.main import create_app
from src.schemas.sync import AuditRecord
from src.services.task_control import TaskSyncControl
from tests.task_runner_fakes import MockBackends, backends
from tests.ui_fakes import NOW, SNAPSHOT, FakeControl, FakeTaskControl

__all__ = ["backends"]

Ports = tuple[FakeTaskControl, httpx.AsyncClient]


@pytest_asyncio.fixture
async def ports() -> AsyncIterator[Ports]:
    app = create_app()
    tasks = FakeTaskControl()
    rows = [
        AuditRecord(created_at=NOW, entity_key="tasks_sync", action="TASK_RUN"),
        AuditRecord(created_at=NOW, entity_key="SO00001", action="DRY_RUN_TASK"),
        *SNAPSHOT.recent,
    ]
    app.state.sync_control = FakeControl(SNAPSHOT.model_copy(update={"recent": rows}))
    app.state.task_control = tasks
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield tasks, client


@pytest.mark.asyncio
async def test_dashboard_renders_task_row_first_in_streams_matrix(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/")).text

    assert (
        html.index('id="streams"') < html.index('id="task-card"') < html.index('id="sync-status"')
    )
    assert 'class="matrix-head label"' in html and 'hx-trigger="load"' not in html


@pytest.mark.asyncio
async def test_card_shows_status_pill_metrics_and_watermarks(ports: Ports) -> None:
    tasks, client = ports
    tasks.state = tasks.state.model_copy(update={"frontu_version": 42})

    response = await client.get("/partials/tasks")

    html = response.text
    assert response.status_code == 200
    assert "Operative Tasks (BC &#8644; Frontu)" in html
    assert '<span id="task-status" class="pill pill-wide pill-idle">IDLE</span>' in html
    assert "DRY RUN" in html and ">2</span>" in html and "7 / 5 / " in html
    assert ">3.2s<" in html
    assert 'id="task-watermarks"' in html and ">42</span>" in html
    assert 'hx-get="/partials/tasks/poll"' not in html


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "pill"), [("FAILED", "pill-failed"), ("STALE", "pill-stale")])
async def test_unhealthy_status_pills(ports: Ports, status: str, pill: str) -> None:
    tasks, client = ports
    tasks.state = tasks.state.model_copy(update={"status": status, "last_run": None})

    html = (await client.get("/partials/tasks")).text

    assert f'class="pill pill-wide {pill}">{status}</span>' in html
    assert "No task runs yet." in html


@pytest.mark.asyncio
async def test_trigger_starts_background_run_and_card_polls(ports: Ports) -> None:
    tasks, client = ports

    first = await client.post("/api/tasks/trigger")
    second = await client.post("/api/tasks/trigger")

    assert tasks.triggers == 2
    assert f"{TASKS_STARTED} (dry run" in first.text
    assert 'hx-get="/partials/tasks/poll" hx-trigger="every 5s"' in first.text
    assert first.headers["HX-Trigger"] == SYNC_STATE_CHANGED
    assert TASKS_ALREADY_RUNNING in second.text


@pytest.mark.asyncio
async def test_poll_keeps_polling_while_running_then_stops_and_refreshes(ports: Ports) -> None:
    tasks, client = ports
    tasks.trigger()

    running = await client.get("/partials/tasks/poll")
    tasks.state = tasks.state.model_copy(update={"running": False, "status": "IDLE"})
    done = await client.get("/partials/tasks/poll")

    assert running.status_code == 200 and "HX-Trigger" not in running.headers
    assert done.status_code == STOP_POLLING
    assert done.headers["HX-Trigger"] == SYNC_STATE_CHANGED


@pytest.mark.asyncio
async def test_toggle_dry_run_and_lock_while_running(ports: Ports) -> None:
    tasks, client = ports

    live = await client.post("/partials/tasks/toggle-dry-run")
    tasks.trigger()
    locked = await client.post("/partials/tasks/toggle-dry-run")

    assert "Task mode set to LIVE" in live.text and ">LIVE<" in live.text
    assert locked.status_code == 409 and TASK_MODE_LOCKED in locked.text


@pytest.mark.asyncio
async def test_audit_table_badges_and_filters_task_rows(ports: Ports) -> None:
    _, client = ports

    html = (await client.get("/partials/audit?action=TASKS&range=all")).text

    assert '<option value="TASKS" selected>Tasks</option>' in html
    assert '<span class="badge badge-ok">TASK</span> TASK_RUN' in html
    assert '<span class="badge badge-neutral">TASK</span> DRY_RUN_TASK' in html
    assert "SN-0001" not in html


@pytest.mark.asyncio
async def test_real_control_runs_in_background_and_reports_last_run(
    backends: MockBackends,
) -> None:
    await init_db()  # the app lifespan creates the schema before the control is used
    control = TaskSyncControl(get_settings())
    await control.prepare()
    try:
        assert (await control.cockpit()).status == "NEVER RUN"
        assert control.trigger() is True
        assert control.trigger() is False
        while control.busy:
            await asyncio.sleep(0.01)
        cockpit = await control.cockpit()
        toggled = await control.toggle_dry_run()
    finally:
        await control.shutdown()
        await get_engine().dispose()

    assert (cockpit.status, cockpit.running, cockpit.dry_run) == ("IDLE", False, True)
    assert cockpit.last_run is not None
    assert (cockpit.last_run.processed, cockpit.last_run.synced) == (2, 2)
    assert cockpit.last_run.finished_at <= datetime.now(UTC)
    assert toggled is not None and toggled.dry_run is False
    assert backends.writes == []


GUID = "3f9c2a1e-0b7d-4c55-9e1a-6d2f8b4a7c10"


@pytest.mark.asyncio
async def test_unified_canary_starts_task_canary_and_refreshes_row(ports: Ports) -> None:
    tasks, client = ports
    order = {"target_module": "order"}

    started = await client.post("/api/canary", data=order | {"record_id": GUID})
    busy = await client.post("/api/tasks/single", data={"target": "SO00001"})
    tasks.state = tasks.state.model_copy(update={"running": False})
    forced = await client.post(
        "/api/canary", data=order | {"record_id": "SO1", "force_live": "true"}
    )
    blank = await client.post("/api/canary", data=order | {"record_id": "  "})

    canaries = [(t.kind, t.value) for t in tasks.canaries]
    assert canaries == [("task", GUID), ("order", "SO00001"), ("order", "SO1")]
    assert tasks.forced == [False, False, True]
    assert started.text.lstrip().startswith('<div id="single-debug"')
    assert f"Canary started for Frontu task {GUID} (dry run" in started.text
    assert '<div id="task-card" hx-swap-oob="true"' in started.text
    assert f"Canary running for Frontu task {GUID}" in started.text
    assert "already in progress" in busy.text
    assert forced.status_code == 200 and "(forced LIVE: writes to BC and Frontu)" in forced.text
    assert blank.status_code == 422 and "Enter a BC Service Order No" in blank.text
