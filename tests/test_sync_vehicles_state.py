"""Vehicle pipeline: cursor window, error-aware cursor progression and run locking."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.resilience import ExternalServiceError
from src.pipelines.sync_vehicles import VehicleSyncPipeline
from tests.pipeline_fakes import (
    INTERNAL_CLIENT,
    NOW,
    FakeBC,
    FakeFrontu,
    FakeSyncRepository,
    vehicle,
)

T1, T2, T3, T4 = (datetime(2026, 10, 20, 10, m, tzinfo=UTC) for m in (1, 2, 3, 4))


def _pipeline(
    bc: FakeBC, repo: FakeSyncRepository, frontu: FakeFrontu | None = None
) -> VehicleSyncPipeline:
    return VehicleSyncPipeline(bc, frontu or FakeFrontu(), repo, INTERNAL_CLIENT, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_first_run_falls_back_to_thirty_days_minus_lookback() -> None:
    bc, repo = FakeBC([]), FakeSyncRepository()

    await _pipeline(bc, repo).run()

    assert bc.since == [NOW - timedelta(days=30) - timedelta(minutes=15)]
    assert repo.statuses == ["RUNNING", "IDLE"]
    assert repo.cursor == NOW - timedelta(days=30)


@pytest.mark.asyncio
async def test_existing_cursor_window_starts_fifteen_minutes_earlier() -> None:
    bc = FakeBC([])

    await _pipeline(bc, FakeSyncRepository(cursor=T2)).run()

    assert bc.since == [T2 - timedelta(minutes=15)]


@pytest.mark.asyncio
async def test_cursor_advances_to_batch_maximum_when_all_succeed() -> None:
    repo = FakeSyncRepository(cursor=T1 - timedelta(hours=1))
    bc = FakeBC([[vehicle("A", T1), vehicle("B", T2)], [vehicle("C", T3)]])

    result = await _pipeline(bc, repo).run()

    assert repo.cursor == T3
    assert result is not None and result.cursor == T3
    assert repo.heartbeats == 2


@pytest.mark.asyncio
async def test_cursor_never_passes_earliest_failed_record() -> None:
    repo = FakeSyncRepository(cursor=T1 - timedelta(hours=1))
    pages = [[vehicle("A", T1), vehicle("B", T2), vehicle("C", T3)], [vehicle("D", T4)]]
    frontu = FakeFrontu(failures={"B": 500})

    await _pipeline(FakeBC(pages), repo, frontu).run()

    assert repo.cursor == T2
    assert [p.systemId for p in frontu.pushed] == ["A", "B", "C", "D"]


@pytest.mark.asyncio
async def test_failure_inside_lookback_never_rewinds_cursor() -> None:
    repo = FakeSyncRepository(cursor=T4)
    frontu = FakeFrontu(failures={"A": 500})

    await _pipeline(FakeBC([[vehicle("A", T3)]]), repo, frontu).run()

    assert repo.cursor == T4


@pytest.mark.asyncio
async def test_fresh_running_claim_blocks_run() -> None:
    bc = FakeBC([[vehicle("A", T1)]])
    repo = FakeSyncRepository(cursor=T1, status="RUNNING", updated_at=NOW - timedelta(minutes=10))

    assert await _pipeline(bc, repo).run() is None
    assert bc.since == []
    assert repo.status == "RUNNING"


@pytest.mark.asyncio
async def test_stale_running_claim_older_than_two_hours_is_reclaimed() -> None:
    bc = FakeBC([])
    repo = FakeSyncRepository(cursor=T1, status="RUNNING", updated_at=NOW - timedelta(hours=3))

    assert await _pipeline(bc, repo).run() is not None
    assert repo.statuses == ["RUNNING", "IDLE"]


@pytest.mark.asyncio
async def test_in_process_lock_rejects_concurrent_run() -> None:
    pipeline = _pipeline(FakeBC([[vehicle("A", T1)]]), FakeSyncRepository(cursor=T1))

    first = asyncio.create_task(pipeline.run())
    await asyncio.sleep(0)
    assert pipeline.is_running

    assert await pipeline.run() is None
    assert await first is not None
    assert not pipeline.is_running


@pytest.mark.asyncio
async def test_source_failure_marks_run_failed_and_keeps_confirmed_progress() -> None:
    repo = FakeSyncRepository(cursor=T1 - timedelta(hours=1))
    bc = FakeBC([[vehicle("A", T1)], [vehicle("B", T2)]], fail_at_page=1)

    with pytest.raises(ExternalServiceError):
        await _pipeline(bc, repo).run()

    assert repo.statuses == ["RUNNING", "FAILED"]
    assert repo.cursor == T1
