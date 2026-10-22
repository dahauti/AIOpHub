"""Vehicle runs in job_runs (RecordedPipeline): Frontu writes, cursor and audit unchanged."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.adapters.resilience import ExternalServiceError
from src.pipelines.recorded_pipeline import RecordedPipeline
from src.pipelines.scheduler import SyncRunner
from src.pipelines.sync_vehicles import VehicleSyncPipeline
from src.repositories.job_run_repository import JobRunRepository
from src.repositories.migration_runner import apply_migrations
from src.schemas.sync import RUNNING
from src.services.run_recorder import RunRecorder
from tests.pipeline_fakes import INTERNAL_CLIENT, NOW, FakeBC, FakeFrontu, vehicle
from tests.repository_fakes import FakeConfigStore, FakeSyncRepository

T0 = NOW - timedelta(hours=2)
T1 = NOW - timedelta(hours=1)


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    db = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'ops.db').as_posix()}")
    await apply_migrations(db)
    yield db
    await db.dispose()


@pytest.fixture
def runs(engine: AsyncEngine) -> JobRunRepository:
    return JobRunRepository(engine)


def _recorded(
    bc: FakeBC, frontu: FakeFrontu, repo: FakeSyncRepository, runs: JobRunRepository, **kw: bool
) -> RecordedPipeline:
    pipeline = VehicleSyncPipeline(bc, frontu, repo, INTERNAL_CLIENT, clock=lambda: NOW, **kw)
    return RecordedPipeline(pipeline, RunRecorder(runs, "onprem_dev"))


def _repo() -> FakeSyncRepository:
    return FakeSyncRepository(cursor=T0 - timedelta(hours=1))


@pytest.mark.asyncio
async def test_live_run_is_recorded_with_counts(runs: JobRunRepository) -> None:
    repo, frontu = _repo(), FakeFrontu()
    bc = FakeBC([[vehicle("SN-1", T0), vehicle("SN-2", T0)]])

    result = await _recorded(bc, frontu, repo, runs, dry_run=False).run(trigger="manual")

    assert result is not None and result.processed == 2
    assert [p.systemId for p in frontu.pushed] == ["SN-1", "SN-2"]
    [run] = await runs.list_runs("workspaces_vehicles")
    assert (run.status, run.trigger_kind, run.mode, run.profile) == (
        "succeeded",
        "manual",
        "LIVE",
        "onprem_dev",
    )
    assert (run.count_ok, run.count_skipped, run.count_failed) == (2, 0, 0)


@pytest.mark.asyncio
async def test_second_run_is_a_new_run_that_skips_unchanged_vehicles(
    runs: JobRunRepository,
) -> None:
    repo, frontu = _repo(), FakeFrontu()
    bc = FakeBC([[vehicle("SN-1", T0), vehicle("SN-2", T0)]])
    recorded = _recorded(bc, frontu, repo, runs, dry_run=False)

    await recorded.run()
    await recorded.run()

    second, first = await runs.list_runs("workspaces_vehicles")
    assert first.run_id != second.run_id and first.trace_id != second.trace_id
    assert [(r.status, r.count_ok, r.count_skipped) for r in (first, second)] == [
        ("succeeded", 2, 0),
        ("succeeded", 0, 2),
    ]
    assert len(frontu.pushed) == 2  # nothing re-sent


@pytest.mark.asyncio
async def test_bc_error_fails_the_run_and_keeps_the_cursor(runs: JobRunRepository) -> None:
    repo = FakeSyncRepository(cursor=T1 - timedelta(hours=1))
    bc = FakeBC([[vehicle("A", T1)], [vehicle("B", NOW)]], fail_at_page=1)

    with pytest.raises(ExternalServiceError):
        await _recorded(bc, FakeFrontu(), repo, runs, dry_run=False).run()

    [run] = await runs.list_runs("workspaces_vehicles")
    assert (run.status, run.error_code, run.count_ok) == ("failed", "system.bc_error", 0)
    assert repo.statuses == ["RUNNING", "FAILED"]
    assert repo.cursor == T1


@pytest.mark.asyncio
async def test_dry_run_is_recorded_as_dry_run(runs: JobRunRepository) -> None:
    frontu = FakeFrontu()

    await _recorded(FakeBC([[vehicle("SN-1", T0)]]), frontu, _repo(), runs, dry_run=True).run()

    [run] = await runs.list_runs("workspaces_vehicles")
    assert (run.mode, run.count_ok) == ("DRY_RUN", 1)
    assert frontu.pushed == []


@pytest.mark.asyncio
async def test_module_claimed_elsewhere_is_recorded_as_skipped(runs: JobRunRepository) -> None:
    repo = FakeSyncRepository(cursor=T0, status=RUNNING, updated_at=NOW)

    assert await _recorded(FakeBC([]), FakeFrontu(), repo, runs).run() is None

    [run] = await runs.list_runs("workspaces_vehicles")
    assert run.status == "skipped"


@pytest.mark.asyncio
async def test_manual_trigger_from_the_dashboard_is_recorded_as_manual(
    runs: JobRunRepository,
) -> None:
    repo = _repo()
    recorded = _recorded(FakeBC([[vehicle("SN-1", T0)]]), FakeFrontu(), repo, runs)
    runner = SyncRunner(recorded, repo, FakeConfigStore(), clock=lambda: NOW)

    assert runner.trigger() is True
    for _ in range(200):  # the run is a background task; wait for its final status
        stored = await runs.list_runs("workspaces_vehicles")
        if stored and stored[0].status != "running":
            break
        await asyncio.sleep(0.01)
    await runner.shutdown()

    [run] = await runs.list_runs("workspaces_vehicles")
    assert (run.trigger_kind, run.status) == ("manual", "succeeded")


@pytest.mark.asyncio
async def test_bookkeeping_failure_never_changes_the_sync(tmp_path: Path) -> None:
    bare = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'bare.db').as_posix()}")
    repo, frontu = _repo(), FakeFrontu()
    bc = FakeBC([[vehicle("SN-1", T0)]])

    result = await _recorded(bc, frontu, repo, JobRunRepository(bare), dry_run=False).run()
    await bare.dispose()

    assert result is not None and result.processed == 1
    assert [p.systemId for p in frontu.pushed] == ["SN-1"]
    assert repo.statuses == ["RUNNING", "IDLE"]
