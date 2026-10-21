"""Glass Box console: pipeline schema, diagnostic event viewer and step-by-step canary telemetry."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.adapters.resilience import ExternalServiceError
from src.api.routes_ui import STOP_POLLING
from src.domain.canary import CanaryTarget
from src.main import create_app
from src.repositories.event_repository import AuditEvent
from src.services.canary_steps import failure_of
from src.services.cockpit_models import CanaryOutcome, LastRun
from src.services.debug_console import DebugConsole, to_debug_event
from src.services.ports import SyncRunResult
from src.services.task_runner import DirectionRun, TaskRunReport
from tests.ui_fakes import NOW, SNAPSHOT, FakeControl, FakeTaskControl

ORDER = CanaryTarget(kind="order", value="SO00001")
URL = "https://api.frontu.example/v2/tasks/abc"
UPSERT = AuditEvent(
    id=3,
    created_at=NOW,
    entity_key="SO00001",
    action="DRY_RUN_TASK",
    payload={"operation": "frontu.upsert_task", "body": {"title": "Fix", "status": 100}},
)
MATERIAL = UPSERT.model_copy(
    update={"id": 4, "payload": {"operation": "frontu.push_material", "body": {"qty": 1}}}
)
FAILED = AuditEvent(
    id=2, created_at=NOW, entity_key="SN-9", action="FAILED", status_code=503, error_message="boom"
)


def _report(dry_run: bool, **counts: int) -> TaskRunReport:
    run = DirectionRun(direction="outbound", result=SyncRunResult(**counts), elapsed_seconds=0.4)
    return TaskRunReport(dry_run=dry_run, runs=[run])


class FakeEvents:
    async def recent(self, limit: int) -> list[AuditEvent]:
        return [MATERIAL, FAILED][:limit]


@pytest_asyncio.fixture
async def setup() -> AsyncIterator[tuple[FakeTaskControl, httpx.AsyncClient]]:
    app = create_app()
    tasks = FakeTaskControl()
    app.state.sync_control = FakeControl(SNAPSHOT)
    app.state.task_control = tasks
    app.state.debug_console = DebugConsole(FakeEvents())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield tasks, client


def test_events_are_levelled_streamed_and_carry_raw_json() -> None:
    task_event, vehicle_event = to_debug_event(MATERIAL), to_debug_event(FAILED)

    assert (task_event.level, task_event.stream) == ("INFO", "Tasks")
    assert "frontu.push_material" in task_event.message and '"qty": 1' in task_event.raw_json
    assert (vehicle_event.level, vehicle_event.stream) == ("ERROR", "Vehicles")
    assert '"status_code": 503' in vehicle_event.raw_json and "boom" in vehicle_event.message
    warn = to_debug_event(FAILED.model_copy(update={"action": "UPSERT", "status_code": 422}))
    assert warn.level == "WARN"


def test_dry_run_canary_steps_show_mapping_and_simulated_writes() -> None:
    outcome = CanaryOutcome.from_report(ORDER, _report(True, synced=1), [UPSERT, MATERIAL])

    titles = [step.title for step in outcome.steps]
    assert titles == ["BC OData fetch", "Mapping evaluation", "Frontu v2 mutation"]
    assert [step.state for step in outcome.steps] == ["ok", "ok", "simulated"]
    assert "status, title" in outcome.steps[1].detail and "1 line" in outcome.steps[1].detail
    assert "frontu.push_material" in outcome.steps[2].detail


def test_missing_order_fails_at_fetch_step() -> None:
    outcome = CanaryOutcome.from_report(ORDER, _report(False, validation_errors=1), [])

    assert [step.state for step in outcome.steps] == ["failed", "skipped", "skipped"]


def test_http_failure_keeps_status_url_and_body() -> None:
    error = ExternalServiceError("frontu", 422, '{"error": "bad status"}', url=URL)

    outcome = CanaryOutcome.from_error(ORDER, error)

    assert outcome.state == "FAILED" and outcome.failure == failure_of(error)
    assert outcome.failure is not None and outcome.failure.url == URL
    assert outcome.failure.status_code == 422 and "bad status" in outcome.failure.body


def test_last_run_splits_latency_per_direction() -> None:
    runs = [
        DirectionRun(direction="outbound", result=SyncRunResult(), elapsed_seconds=1.5),
        DirectionRun(direction="inbound", result=SyncRunResult(), elapsed_seconds=0.5),
    ]
    last = LastRun.from_report(NOW, TaskRunReport(dry_run=True, runs=runs))

    assert (last.outbound_seconds, last.inbound_seconds, last.elapsed_seconds) == (1.5, 0.5, 2.0)


@pytest.mark.asyncio
async def test_dashboard_has_pipeline_below_diagnostics_and_console_at_bottom(
    setup: tuple[FakeTaskControl, httpx.AsyncClient],
) -> None:
    tasks, client = setup
    tasks.state = tasks.state.model_copy(
        update={"bc_company": "ALWARK", "frontu_base_url": URL, "frontu_version": 42}
    )

    html = (await client.get("/")).text

    assert html.index('id="overview"') < html.index('id="pipeline-schema"')
    assert html.index('id="pipeline-schema"') < html.index('id="canary-bar"')
    assert html.index('id="audit-zone"') < html.index('id="debug-console"')
    for text in ("ALWARK", "Status Mapping &amp; Rules", "api.frontu.example", ">42<"):
        assert text in html
    assert "frontu.push_material" in html and "<details" in html and "ERROR" in html


@pytest.mark.asyncio
async def test_pipeline_and_console_partials_refresh(
    setup: tuple[FakeTaskControl, httpx.AsyncClient],
) -> None:
    _, client = setup

    pipeline = await client.get("/partials/pipeline")
    console = await client.get("/partials/debug-console")

    assert pipeline.status_code == 200 and pipeline.text.count('class="gb-node gb-node-') == 3
    assert console.status_code == 200 and console.text.count('class="gb-event gb-level-') == 2


@pytest.mark.asyncio
async def test_canary_steps_poll_while_running_then_stop(
    setup: tuple[FakeTaskControl, httpx.AsyncClient],
) -> None:
    tasks, client = setup
    tasks.trigger_single(ORDER)

    running = await client.get("/partials/canary/steps")
    error = ExternalServiceError("frontu", 422, '{"error": "bad status"}', url=URL)
    tasks.state = tasks.state.model_copy(update={"canary": CanaryOutcome.from_error(ORDER, error)})
    failed = await client.get("/partials/canary/steps")

    assert running.status_code == 200 and 'hx-get="/partials/canary/steps"' in running.text
    assert failed.status_code == STOP_POLLING and "hx-trigger" not in failed.text
    for text in ("HTTP 422", URL, "bad status"):
        assert text in failed.text
