"""Weekly full sync: ignores the cursor window but keeps fingerprint skipping."""

from datetime import UTC, datetime, timedelta

import pytest

from src.pipelines.sync_vehicles import FULL_SYNC_FROM, LOOKBACK, VehicleSyncPipeline
from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from tests.pipeline_fakes import (
    FIELD_IDS,
    INTERNAL_CLIENT,
    NOW,
    FakeBC,
    FakeFrontu,
    FakeSyncRepository,
    vehicle,
)

CURSOR = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
OLD = datetime(2019, 3, 1, tzinfo=UTC)


def _pipeline(bc: FakeBC, frontu: FakeFrontu, repo: FakeSyncRepository) -> VehicleSyncPipeline:
    return VehicleSyncPipeline(bc, frontu, repo, INTERNAL_CLIENT, dry_run=False, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_full_run_reads_from_the_beginning_and_pushes_only_changed_or_unknown() -> None:
    unchanged, edited, fresh = vehicle("SN-1", OLD), vehicle("SN-2", OLD), vehicle("SN-3", OLD)
    repo, frontu = FakeSyncRepository(cursor=CURSOR), FakeFrontu()
    repo.fingerprints = {
        "SN-1": payload_hash(build_workspace(unchanged, None, FIELD_IDS, INTERNAL_CLIENT)),
        "SN-2": "stale-hash",
    }
    bc = FakeBC([[unchanged, edited, fresh]])

    result = await _pipeline(bc, frontu, repo).run(full=True)

    assert bc.since == [FULL_SYNC_FROM]
    assert [p.systemId for p in frontu.pushed] == ["SN-2", "SN-3"]
    assert result is not None and (result.full, result.processed, result.skipped) == (True, 2, 1)
    assert repo.cursor == CURSOR  # old records never move the cursor backwards


@pytest.mark.asyncio
async def test_incremental_run_keeps_the_cursor_window() -> None:
    bc = FakeBC([[vehicle("SN-1", CURSOR + timedelta(minutes=1))]])

    result = await _pipeline(bc, FakeFrontu(), FakeSyncRepository(cursor=CURSOR)).run()

    assert bc.since == [CURSOR - LOOKBACK]
    assert result is not None and result.full is False
