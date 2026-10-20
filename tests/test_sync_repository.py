"""SqlSyncRepository integration tests against a real temp SQLite database."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.database import get_engine, get_sessionmaker, init_db
from src.models.audit import SyncAuditLog
from src.pipelines.sync_vehicles import VehicleSyncPipeline
from src.repositories.sync_repository import SqlSyncRepository
from src.schemas.sync import AuditEntry
from tests.pipeline_fakes import INTERNAL_CLIENT, FakeBC, FakeFrontu, vehicle
from tests.pipeline_fakes import NOW as PIPELINE_NOW

MODULE = "workspaces_vehicles"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
INITIAL = NOW - timedelta(days=30)


@pytest_asyncio.fixture
async def repo(temp_database: Path) -> AsyncIterator[SqlSyncRepository]:
    await init_db()
    yield SqlSyncRepository(get_sessionmaker(), MODULE)
    await get_engine().dispose()


@pytest.mark.asyncio
async def test_first_claim_creates_state_with_aware_utc_cursor(repo: SqlSyncRepository) -> None:
    assert await repo.try_acquire(NOW, INITIAL) is True

    cursor = await repo.get_cursor()
    snapshot = await repo.dashboard_snapshot(NOW, 15)
    assert cursor == INITIAL
    assert cursor.tzinfo is UTC
    assert snapshot.status == "RUNNING"


@pytest.mark.asyncio
async def test_claim_is_exclusive_until_released_or_stale(repo: SqlSyncRepository) -> None:
    assert await repo.try_acquire(NOW, INITIAL) is True
    assert await repo.try_acquire(NOW + timedelta(minutes=5), INITIAL) is False

    await repo.heartbeat(NOW + timedelta(hours=1))
    assert await repo.try_acquire(NOW + timedelta(hours=2, minutes=30), INITIAL) is False
    assert await repo.try_acquire(NOW + timedelta(hours=3, minutes=1), INITIAL) is True

    await repo.release("IDLE", NOW + timedelta(hours=4))
    assert (await repo.dashboard_snapshot(NOW, 15)).status == "IDLE"
    assert await repo.try_acquire(NOW + timedelta(hours=4, minutes=1), INITIAL) is True


@pytest.mark.asyncio
async def test_cursor_only_moves_forward(repo: SqlSyncRepository) -> None:
    await repo.try_acquire(NOW, INITIAL)

    await repo.advance_cursor(NOW, NOW)
    await repo.advance_cursor(NOW - timedelta(days=1), NOW)

    assert await repo.get_cursor() == NOW


@pytest.mark.asyncio
async def test_get_cursor_without_state_fails_fast(repo: SqlSyncRepository) -> None:
    with pytest.raises(RuntimeError, match="acquire the module first"):
        await repo.get_cursor()


@pytest.mark.asyncio
async def test_fingerprints_upsert_read_delete_and_module_scope(
    repo: SqlSyncRepository,
) -> None:
    other = SqlSyncRepository(get_sessionmaker(), "other_module")
    await repo.save_fingerprint("SN-1", "a" * 64, NOW)
    await repo.save_fingerprint("SN-1", "b" * 64, NOW)
    await repo.save_fingerprint("SN-2", "c" * 64, NOW)
    await other.save_fingerprint("SN-1", "z" * 64, NOW)

    assert await repo.get_fingerprints(["SN-1", "SN-3"]) == {"SN-1": "b" * 64}
    assert await repo.get_fingerprints([]) == {}

    await repo.delete_fingerprint("SN-1")
    assert await repo.get_fingerprints(["SN-1", "SN-2"]) == {"SN-2": "c" * 64}
    assert await other.get_fingerprints(["SN-1"]) == {"SN-1": "z" * 64}


@pytest.mark.asyncio
async def test_audit_rows_feed_counters_and_recent_list(repo: SqlSyncRepository) -> None:
    actions = [
        "UPSERT",
        "ARCHIVE",
        "SKIPPED",
        "FAILED",
        "DRY_RUN_UPSERT",
        "DRY_RUN_ARCHIVE",
        "SINGLE_UPSERT",
        "SINGLE_ARCHIVE",
        "DRY_RUN_SINGLE_UPSERT",
        "DRY_RUN_SINGLE_ARCHIVE",
        "ADOPT",
        "SINGLE_ADOPT",
        "DRY_RUN_ADOPT",
        "DRY_RUN_SINGLE_ADOPT",
        "FAILED_AMBIGUOUS_MATCH",
    ]
    for index, action in enumerate(actions):
        await repo.record_audit(
            AuditEntry.model_validate(
                {"entity_key": f"SN-{index}", "action": action, "payload": {"n": index}}
            )
        )
    since = datetime.now(UTC) - timedelta(hours=1)

    snapshot = await repo.dashboard_snapshot(since, limit=4)
    future = await repo.dashboard_snapshot(datetime.now(UTC) + timedelta(hours=1), limit=4)

    assert (snapshot.processed, snapshot.skipped, snapshot.errors, snapshot.planned) == (6, 1, 2, 6)
    assert [r.entity_key for r in snapshot.recent] == ["SN-14", "SN-13", "SN-12", "SN-11"]
    assert all(r.created_at.tzinfo is UTC for r in snapshot.recent)
    assert (future.processed, future.planned) == (0, 0)
    async with get_sessionmaker()() as session:
        payload = await session.scalar(select(SyncAuditLog.payload).where(SyncAuditLog.id == 1))
    assert payload == {"n": 0}


@pytest.mark.asyncio
async def test_snapshot_without_state_reports_idle(repo: SqlSyncRepository) -> None:
    snapshot = await repo.dashboard_snapshot(NOW, 15)

    assert (snapshot.status, snapshot.cursor_timestamp, snapshot.recent) == ("IDLE", None, [])


@pytest.mark.asyncio
async def test_pipeline_round_trip_on_sqlite(repo: SqlSyncRepository) -> None:
    modified = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    bc, frontu = FakeBC([[vehicle("SN-1", modified)]]), FakeFrontu()
    pipeline = VehicleSyncPipeline(
        bc, frontu, repo, INTERNAL_CLIENT, dry_run=False, clock=lambda: PIPELINE_NOW
    )

    first = await pipeline.run()
    second = await pipeline.run()

    assert first is not None and first.processed == 1
    assert second is not None and second.skipped == 1
    assert len(frontu.pushed) == 1
    assert await repo.get_cursor() == modified
    assert bc.since[1] == modified - timedelta(minutes=15)
    assert (await repo.dashboard_snapshot(NOW - timedelta(days=1), 15)).status == "IDLE"
