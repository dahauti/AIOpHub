"""Task runs end to end: job_runs + record_outcomes, with BC/Frontu writes left unchanged."""

import pytest
from sqlalchemy.exc import OperationalError

from src.adapters.resilience import ExternalServiceError
from src.config import get_settings
from src.database import get_engine
from src.domain.canary import CanaryTarget
from src.repositories.job_run_repository import JobRunRepository
from src.schemas.operations import JobRun, RecordOutcome
from src.services.task_runner import run_single, run_tasks
from src.storage.task_repository import TaskRepository
from src.storage.task_state_repository import TaskStateRepository
from tests.task_runner_fakes import FRONTU_GETS, MockBackends, backends

__all__ = ["backends"]

LIVE_WRITES = [
    ("POST", "tasks"),
    ("POST", "materials"),
    ("PATCH", "TG_Lines(Document_Type='Order',DocumentNo='SO00001',LineNo=10000)"),
    ("PATCH", "ServiceOrders(Document_Type='Order',No='SO00001')"),
]


@pytest.fixture
def live(backends: MockBackends, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live mode; after `backends`, whose isolated env would otherwise clear the variable."""
    monkeypatch.setenv("SYNC_DRY_RUN", "false")
    get_settings.cache_clear()


async def _stored() -> list[tuple[JobRun, list[RecordOutcome]]]:
    """Task runs oldest first, each with its outcomes."""
    repo = JobRunRepository(get_engine())
    try:
        runs = list(reversed(await repo.list_runs("tasks_sync")))
        return [(run, await repo.list_outcomes(run.run_id)) for run in runs]
    finally:
        await get_engine().dispose()


def _writes(backends: MockBackends) -> list[tuple[str, str]]:
    return [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in backends.writes]


@pytest.mark.asyncio
@pytest.mark.usefixtures("live")
async def test_live_run_is_recorded_and_writes_are_unchanged(backends: MockBackends) -> None:
    report = await run_tasks(get_settings(), "outbound")

    assert report is not None
    assert _writes(backends) == LIVE_WRITES
    [(run, outcomes)] = await _stored()
    assert (run.status, run.trigger_kind, run.mode, run.profile) == (
        "succeeded",
        "manual",
        "LIVE",
        "onprem_dev",
    )
    assert (run.count_ok, run.count_skipped, run.count_failed) == (1, 0, 0)
    assert [(o.object_type, o.object_id, o.result) for o in outcomes] == [
        ("bc_service_order", "SO00001", "ok")
    ]
    assert outcomes[0].trace_id == run.trace_id


@pytest.mark.asyncio
async def test_dry_run_is_recorded_as_dry_run_without_writes(backends: MockBackends) -> None:
    await run_tasks(get_settings(), "sync")

    [(run, outcomes)] = await _stored()
    assert (run.status, run.mode, run.count_ok) == ("succeeded", "DRY_RUN", 2)
    assert [o.object_id for o in outcomes] == ["SO00001"]
    assert backends.writes == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("live")
async def test_running_twice_gives_two_runs_and_one_outcome_per_order_each(
    backends: MockBackends,
) -> None:
    settings = get_settings()

    await run_tasks(settings, "outbound")
    await run_tasks(settings, "outbound")

    stored = await _stored()
    assert [run.status for run, _ in stored] == ["succeeded", "succeeded"]
    assert len({run.run_id for run, _ in stored}) == len({run.trace_id for run, _ in stored}) == 2
    assert [[(o.object_id, o.result) for o in outcomes] for _, outcomes in stored] == [
        [("SO00001", "ok")],
        [("SO00001", "ok")],
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("live")
async def test_bc_api_error_fails_the_run_without_partial_state(backends: MockBackends) -> None:
    backends.fail_bc_reads = True

    with pytest.raises(ExternalServiceError):
        await run_tasks(get_settings(), "outbound")

    [(run, outcomes)] = await _stored()
    assert (run.status, run.error_code) == ("failed", "system.bc_error")
    assert run.error_summary is not None and "400" in run.error_summary
    assert outcomes == [] and backends.writes == []
    engine = get_engine()
    try:
        assert await TaskRepository(engine).get_raw_watermark("operative_tasks_bc") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("live")
async def test_no_client_match_is_a_data_error_outcome(
    backends: MockBackends, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(FRONTU_GETS, "/clients", [])

    await run_tasks(get_settings(), "outbound")

    [(run, [outcome])] = await _stored()
    assert (run.status, run.count_failed) == ("succeeded", 1)
    assert (outcome.result, outcome.error_code, outcome.error_type, outcome.params) == (
        "error",
        "task.no_client_match",
        "data",
        {"customer_no": "C00010"},
    )
    assert backends.writes == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("live")
async def test_error_before_the_frontu_task_exists_is_recorded(backends: MockBackends) -> None:
    backends.frontu_write_status = 422

    await run_tasks(get_settings(), "outbound")

    [(run, [outcome])] = await _stored()
    assert (run.status, run.count_failed) == ("succeeded", 1)
    assert (outcome.result, outcome.error_code, outcome.error_type, outcome.http_status) == (
        "error",
        "system.frontu_error",
        "system",
        422,
    )
    assert outcome.technical_detail is not None and "422" in outcome.technical_detail
    engine = get_engine()
    try:
        assert await TaskStateRepository(engine).get_by_order("SO00001") is None  # as before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_canary_run_has_canary_trigger(backends: MockBackends) -> None:
    await run_single(get_settings(), CanaryTarget(kind="order", value="SO00001"))

    [(run, outcomes)] = await _stored()
    assert (run.trigger_kind, run.mode) == ("canary", "DRY_RUN")
    assert [(o.object_id, o.result) for o in outcomes] == [("SO00001", "ok")]


@pytest.mark.asyncio
@pytest.mark.usefixtures("live")
async def test_bookkeeping_failure_never_changes_the_sync(
    backends: MockBackends, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object) -> None:
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    for method in ("start_run", "finish_run", "add_outcome"):
        monkeypatch.setattr(JobRunRepository, method, broken)

    report = await run_tasks(get_settings(), "outbound")

    assert report is not None and report.runs[0].result.synced == 1
    assert _writes(backends) == LIVE_WRITES
