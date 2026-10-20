"""Legacy workspace adoption in the batch and single-vehicle pipelines (fake adapters)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.pipelines.single_vehicle import SINGLE_INDEX_MAX_AGE, SingleVehicleSync
from src.pipelines.sync_vehicles import VehicleSyncPipeline
from src.pipelines.vehicle_mapping import payload_hash
from tests.pipeline_fakes import (
    INTERNAL_CLIENT,
    NOW,
    FakeBC,
    FakeFrontu,
    FakeSyncRepository,
    vehicle,
    workspace,
)

T0 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
CURSOR = T0 - timedelta(hours=1)
LEGACY = workspace("legacy-7", "vin-1")


def _repo() -> FakeSyncRepository:
    return FakeSyncRepository(cursor=CURSOR)


def _batch(
    bc: FakeBC, frontu: FakeFrontu, repo: FakeSyncRepository, *, dry_run: bool = False
) -> VehicleSyncPipeline:
    return VehicleSyncPipeline(
        bc, frontu, repo, INTERNAL_CLIENT, dry_run=dry_run, clock=lambda: NOW
    )


def _single(bc: FakeBC, frontu: FakeFrontu, *, dry_run: bool = False) -> SingleVehicleSync:
    return SingleVehicleSync(
        bc,
        frontu,
        _repo(),
        INTERNAL_CLIENT,
        lock=asyncio.Lock(),
        dry_run=dry_run,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_batch_adopts_unique_legacy_match_instead_of_creating() -> None:
    repo, frontu = _repo(), FakeFrontu(workspaces=[LEGACY])

    result = await _batch(FakeBC([[vehicle("SN-1", T0, vin="VIN-1")]]), frontu, repo).run()

    assert result is not None and result.processed == 1
    assert frontu.pushed == []
    [(workspace_id, payload)] = frontu.adopted
    assert (workspace_id, payload.systemId, payload.serialNumber) == ("legacy-7", "SN-1", "VIN-1")
    assert repo.fingerprints == {"SN-1": payload_hash(payload)}
    assert (repo.audit[0].action, repo.audit[0].status_code) == ("ADOPT", 200)


@pytest.mark.asyncio
async def test_batch_dry_run_reports_planned_adoption_without_writing() -> None:
    repo, frontu = _repo(), FakeFrontu(workspaces=[LEGACY])
    bc = FakeBC([[vehicle("SN-1", T0, vin="VIN-1"), vehicle("SN-2", T0, vin="NEW")]])

    result = await _batch(bc, frontu, repo, dry_run=True).run()

    assert result is not None and result.planned == 2
    assert frontu.pushed == [] and frontu.adopted == []
    assert [e.action for e in repo.audit] == ["DRY_RUN_ADOPT", "DRY_RUN_UPSERT"]


@pytest.mark.asyncio
async def test_batch_ambiguous_match_fails_closed_without_holding_cursor() -> None:
    frontu = FakeFrontu(workspaces=[workspace("a", "VIN-1"), workspace("b", "vin-1")])
    pages = [[vehicle("SN-1", T0, vin="VIN-1"), vehicle("SN-2", T0 + timedelta(minutes=5))]]
    repo = _repo()

    result = await _batch(FakeBC(pages), frontu, repo).run()

    assert result is not None and (result.failed, result.processed) == (1, 1)
    assert frontu.adopted == [] and [p.systemId for p in frontu.pushed] == ["SN-2"]
    failed = repo.audit[0]
    assert (failed.entity_key, failed.action) == ("SN-1", "FAILED_AMBIGUOUS_MATCH")
    assert failed.error_message is not None and "a, b" in failed.error_message
    assert failed.payload is not None and failed.payload["systemId"] == "SN-1"
    assert repo.cursor == T0 + timedelta(minutes=5)
    assert "SN-1" not in repo.fingerprints


@pytest.mark.asyncio
async def test_batch_loads_index_once_per_run_and_only_when_needed() -> None:
    frontu = FakeFrontu(workspaces=[LEGACY])
    pages = [[vehicle("SN-1", T0)], [vehicle("SN-2", T0 + timedelta(minutes=1))]]
    pipeline = _batch(FakeBC(pages), frontu, _repo())

    await pipeline.run()
    assert frontu.list_max_ages == [0.0]

    await pipeline.run()
    assert frontu.list_max_ages == [0.0]


@pytest.mark.asyncio
async def test_batch_index_failure_fails_the_vehicle_without_writing() -> None:
    frontu, repo = FakeFrontu(), _repo()
    frontu.list_failure = 503

    result = await _batch(FakeBC([[vehicle("SN-1", T0)]]), frontu, repo).run()

    assert result is not None and result.failed == 1
    assert frontu.pushed == [] and repo.audit[0].status_code == 503


@pytest.mark.asyncio
async def test_blocked_vehicle_is_archived_by_system_id_not_adopted() -> None:
    frontu = FakeFrontu(workspaces=[LEGACY])

    await _batch(FakeBC([[vehicle("SN-1", T0, vin="VIN-1", blocked=True)]]), frontu, _repo()).run()

    assert frontu.adopted == []
    assert [(p.systemId, p.deleted) for p in frontu.pushed] == [("SN-1", True)]


@pytest.mark.asyncio
async def test_single_sync_adopts_legacy_workspace() -> None:
    frontu = FakeFrontu(workspaces=[LEGACY])

    result = await _single(
        FakeBC([[vehicle("SN-1", T0, vin="VIN-1")]]), frontu
    ).sync_single_vehicle("SN-1")

    assert (result.outcome, result.action, result.status_code) == ("SYNCED", "SINGLE_ADOPT", 200)
    assert [w for w, _ in frontu.adopted] == ["legacy-7"] and frontu.pushed == []
    assert frontu.list_max_ages == [SINGLE_INDEX_MAX_AGE]


@pytest.mark.asyncio
async def test_single_sync_dry_run_plans_adoption() -> None:
    frontu = FakeFrontu(workspaces=[LEGACY])
    bc = FakeBC([[vehicle("SN-1", T0, vin="VIN-1")]])

    result = await _single(bc, frontu, dry_run=True).sync_single_vehicle("SN-1")

    assert (result.outcome, result.action) == ("DRY_RUN", "DRY_RUN_SINGLE_ADOPT")
    assert frontu.adopted == []


@pytest.mark.asyncio
async def test_single_sync_ambiguous_match_fails_with_candidates() -> None:
    frontu = FakeFrontu(workspaces=[workspace("a", "SN-1"), workspace("b", "SN-1")])

    result = await _single(FakeBC([[vehicle("SN-1", T0)]]), frontu).sync_single_vehicle("SN-1")

    assert (result.outcome, result.action) == ("FAILED", "FAILED_AMBIGUOUS_MATCH")
    assert result.error is not None and "a, b" in result.error
    assert frontu.pushed == [] and frontu.adopted == []
