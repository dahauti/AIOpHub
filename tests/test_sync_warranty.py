"""Warranty enrichment in the batch and single-vehicle pipelines (fake adapters)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.pipelines.single_vehicle import SingleVehicleSync
from src.pipelines.sync_vehicles import VehicleSyncPipeline
from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from tests.pipeline_fakes import (
    FIELD_IDS,
    INTERNAL_CLIENT,
    NOW,
    FakeBC,
    FakeFrontu,
    FakeSyncRepository,
    vehicle,
    warranty,
)

T0 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
WARRANTIES = {
    "SN-1": [
        warranty("SN-1", start="2025-01-15", formula="1Y", run=2000),
        warranty("SN-1", code="EXTENDED_FACTORY", start="2026-01-15", formula="1Y+6M", run=4000),
        warranty("SN-1", code="NOWARRANTY", start="2020-01-01", formula="9Y", run=99999),
    ]
}


def _repo() -> FakeSyncRepository:
    return FakeSyncRepository(cursor=T0 - timedelta(hours=1))


def _batch(bc: FakeBC, frontu: FakeFrontu, repo: FakeSyncRepository) -> VehicleSyncPipeline:
    return VehicleSyncPipeline(bc, frontu, repo, INTERNAL_CLIENT, dry_run=False, clock=lambda: NOW)


def _single(bc: FakeBC, frontu: FakeFrontu, repo: FakeSyncRepository) -> SingleVehicleSync:
    return SingleVehicleSync(
        bc, frontu, repo, INTERNAL_CLIENT, lock=asyncio.Lock(), dry_run=False, clock=lambda: NOW
    )


def _assert_enriched(payload: FrontuWorkspaceUpsert) -> None:
    assert payload.warrantyStartDate == "2025-01-15"
    assert payload.warrantyEndDate == "2026-01-15"
    assert payload.extendedWarrantyEndDate == "2027-07-15"
    assert payload.warrantyIndicatorLimit == 4000


@pytest.mark.asyncio
async def test_batch_pushes_resolved_warranty_and_fingerprints_it() -> None:
    bc, frontu, repo = FakeBC([[vehicle("SN-1", T0)]], warranties=WARRANTIES), FakeFrontu(), _repo()

    result = await _batch(bc, frontu, repo).run()

    assert result is not None and result.processed == 1
    _assert_enriched(frontu.pushed[0])
    assert repo.fingerprints["SN-1"] == payload_hash(frontu.pushed[0])
    audit_payload = repo.audit[0].payload
    assert audit_payload is not None and audit_payload["warrantyIndicatorLimit"] == 4000


@pytest.mark.asyncio
async def test_batch_caches_warranty_lookup_per_serial_within_a_run() -> None:
    pages = [[vehicle("SN-1", T0)], [vehicle("SN-1", T0 + timedelta(minutes=1))]]
    bc, frontu = FakeBC(pages, warranties=WARRANTIES), FakeFrontu()

    result = await _batch(bc, frontu, _repo()).run()

    assert bc.warranty_calls == ["SN-1"]
    assert result is not None and (result.processed, result.skipped) == (1, 1)


@pytest.mark.asyncio
async def test_batch_cache_does_not_survive_into_the_next_run() -> None:
    bc, repo = FakeBC([[vehicle("SN-1", T0)]], warranties=WARRANTIES), _repo()
    pipeline = _batch(bc, FakeFrontu(), repo)

    await pipeline.run()
    await pipeline.run()

    assert bc.warranty_calls == ["SN-1", "SN-1"]


@pytest.mark.asyncio
async def test_batch_warranty_change_defeats_the_stored_fingerprint() -> None:
    source = vehicle("SN-1", T0)
    repo, frontu = _repo(), FakeFrontu()
    repo.fingerprints["SN-1"] = payload_hash(
        build_workspace(source, None, FIELD_IDS, INTERNAL_CLIENT)
    )

    result = await _batch(FakeBC([[source]], warranties=WARRANTIES), frontu, repo).run()

    assert result is not None and (result.processed, result.skipped) == (1, 0)
    _assert_enriched(frontu.pushed[0])


@pytest.mark.asyncio
async def test_batch_warranty_lookup_failure_fails_only_that_vehicle() -> None:
    pages = [[vehicle("SN-1", T0), vehicle("SN-2", T0 + timedelta(minutes=1))]]
    bc, frontu, repo = FakeBC(pages), FakeFrontu(), _repo()
    bc.failing_warranties = frozenset({"SN-1"})

    result = await _batch(bc, frontu, repo).run()

    assert result is not None and (result.failed, result.processed) == (1, 1)
    assert [p.systemId for p in frontu.pushed] == ["SN-2"]
    failed = next(e for e in repo.audit if e.action == "FAILED")
    assert (failed.entity_key, failed.status_code) == ("SN-1", 503)
    assert repo.cursor == T0


@pytest.mark.asyncio
async def test_single_sync_pushes_resolved_warranty() -> None:
    bc, frontu = FakeBC([[vehicle("SN-1", T0)]], warranties=WARRANTIES), FakeFrontu()

    result = await _single(bc, frontu, _repo()).sync_single_vehicle("SN-1")

    assert result.outcome == "SYNCED"
    assert bc.warranty_calls == ["SN-1"]
    _assert_enriched(frontu.pushed[0])


@pytest.mark.asyncio
async def test_single_sync_skips_warranty_lookup_when_vehicle_is_missing() -> None:
    bc = FakeBC([[]], warranties=WARRANTIES)

    result = await _single(bc, FakeFrontu(), _repo()).sync_single_vehicle("SN-404")

    assert result.outcome == "NOT_FOUND"
    assert bc.warranty_calls == []


@pytest.mark.asyncio
async def test_single_sync_warranty_lookup_failure_is_reported() -> None:
    bc, frontu, repo = FakeBC([[vehicle("SN-1", T0)]]), FakeFrontu(), _repo()
    bc.failing_warranties = frozenset({"SN-1"})

    result = await _single(bc, frontu, repo).sync_single_vehicle("SN-1")

    assert (result.outcome, result.status_code) == ("FAILED", 503)
    assert frontu.pushed == []
    assert repo.audit[-1].action == "FAILED"
