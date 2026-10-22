"""RunRecorder: job_runs lifecycle, outcomes, trace IDs, sanitising, never breaking a sync."""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.adapters.resilience import ExternalServiceError
from src.repositories.job_run_repository import JobRunRepository
from src.repositories.migration_runner import apply_migrations
from src.services.run_recorder import RunRecorder

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzdmMifQ.c2lnbmF0dXJl"
CONN_PASSWORD = "S3cr3t-pw"
LEAKY = (
    f"frontu request failed (status=401): Authorization: Bearer {TOKEN} "
    f"db=postgresql+asyncpg://svc:{CONN_PASSWORD}@db01:5432/aio"
)


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    db = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'ops.db').as_posix()}")
    await apply_migrations(db)
    yield db
    await db.dispose()


@pytest.fixture
def repo(engine: AsyncEngine) -> JobRunRepository:
    return JobRunRepository(engine)


@pytest.fixture
def recorder(repo: JobRunRepository) -> RunRecorder:
    return RunRecorder(repo, "onprem_dev")


@pytest.mark.asyncio
async def test_successful_run_records_counts_and_outcomes(
    recorder: RunRecorder, repo: JobRunRepository
) -> None:
    async with recorder.run("tasks_sync", "manual", "LIVE") as run:
        await recorder.record_outcome("bc_service_order", "SO1", "ok")
        await recorder.record_outcome(
            "bc_service_order",
            "SO2",
            "error",
            error_code="task.no_client_match",
            params={"customer_no": "C9"},
        )
        run.count_ok, run.count_failed = 1, 1

    [stored] = await repo.list_runs("tasks_sync")
    assert (stored.status, stored.trigger_kind, stored.mode, stored.profile) == (
        "succeeded",
        "manual",
        "LIVE",
        "onprem_dev",
    )
    assert (stored.count_ok, stored.count_failed, stored.trace_id) == (1, 1, run.trace_id)
    assert stored.finished_at is not None and stored.finished_at >= stored.started_at
    ok, error = await repo.list_outcomes(run.run_id)
    assert (ok.object_id, ok.result, ok.error_code, ok.error_type) == ("SO1", "ok", None, None)
    assert (error.error_code, error.error_type, error.params) == (
        "task.no_client_match",
        "data",
        {"customer_no": "C9"},
    )
    assert error.trace_id == run.trace_id


@pytest.mark.asyncio
async def test_same_object_twice_in_one_run_is_stored_once(
    recorder: RunRecorder, repo: JobRunRepository
) -> None:
    async with recorder.run("tasks_sync", "manual", "LIVE") as run:
        await recorder.record_outcome("bc_service_order", "SO1", "ok")
        await recorder.record_outcome("bc_service_order", "SO1", "ok")

    assert len(await repo.list_outcomes(run.run_id)) == 1


@pytest.mark.asyncio
async def test_failed_run_is_recorded_and_the_error_re_raised(
    recorder: RunRecorder, repo: JobRunRepository
) -> None:
    error = ExternalServiceError("business-central", 503, "unavailable")

    with pytest.raises(ExternalServiceError) as raised:
        async with recorder.run("workspaces_vehicles", "schedule", "DRY_RUN"):
            raise error

    assert raised.value is error
    [stored] = await repo.list_runs("workspaces_vehicles")
    assert (stored.status, stored.error_code) == ("failed", "system.bc_error")
    assert stored.error_summary is not None and "unavailable" in stored.error_summary


@pytest.mark.asyncio
async def test_secrets_never_reach_the_database(recorder: RunRecorder, engine: AsyncEngine) -> None:
    leak = ExternalServiceError("frontu", 401, LEAKY)

    with pytest.raises(ExternalServiceError):
        async with recorder.run("tasks_sync", "manual", "LIVE"):
            await recorder.record_outcome("bc_service_order", "SO1", "error", error=leak)
            raise leak

    async with engine.connect() as connection:
        rows = [
            str(tuple(row))
            for table in ("job_runs", "record_outcomes")
            for row in await connection.execute(text(f"SELECT * FROM {table}"))
        ]
    dump = " ".join(rows)
    assert "system.frontu_error" in dump and "[REDACTED]" in dump
    assert TOKEN not in dump
    assert CONN_PASSWORD not in dump and "db01:5432" not in dump


@pytest.mark.asyncio
async def test_cancelled_run_is_aborted(recorder: RunRecorder, repo: JobRunRepository) -> None:
    with pytest.raises(asyncio.CancelledError):
        async with recorder.run("workspaces_vehicles", "manual", "LIVE"):
            raise asyncio.CancelledError

    assert [r.status for r in await repo.list_runs("workspaces_vehicles")] == ["aborted"]


@pytest.mark.asyncio
async def test_skipped_flag_and_late_mode_are_stored(
    recorder: RunRecorder, repo: JobRunRepository
) -> None:
    async with recorder.run("workspaces_vehicles", "schedule", "LIVE") as run:
        run.skipped, run.mode = True, "DRY_RUN"

    [stored] = await repo.list_runs("workspaces_vehicles")
    assert (stored.status, stored.mode) == ("skipped", "DRY_RUN")


@pytest.mark.asyncio
async def test_outcome_outside_a_run_is_ignored(recorder: RunRecorder, engine: AsyncEngine) -> None:
    await recorder.record_outcome("bc_service_order", "SO1", "ok")

    async with engine.connect() as connection:
        count = (await connection.execute(text("SELECT COUNT(*) FROM record_outcomes"))).scalar()
    assert count == 0


@pytest.mark.asyncio
async def test_persistence_failure_never_breaks_the_sync(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # No migrations applied: every bookkeeping write fails with "no such table".
    bare = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'bare.db').as_posix()}")
    recorder = RunRecorder(JobRunRepository(bare), "onprem_dev")
    work_done = False

    with caplog.at_level(logging.WARNING):
        async with recorder.run("tasks_sync", "manual", "LIVE") as run:
            await recorder.record_outcome("bc_service_order", "SO1", "ok")
            work_done = True
            run.count_ok = 1
    await bare.dispose()

    assert work_done
    failures = [r for r in caplog.records if "Run bookkeeping" in r.getMessage()]
    assert {r.getMessage().split(")")[0] for r in failures} == {
        "Run bookkeeping (start",
        "Run bookkeeping (outcome",
        "Run bookkeeping (finish",
    }
