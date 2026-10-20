"""Vehicle pipeline: enrichment, fingerprint skipping, archiving and audit (fake adapters)."""

from datetime import UTC, datetime, timedelta

import pytest

from src.pipelines.sync_vehicles import VehicleSyncPipeline
from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle
from tests.pipeline_fakes import (
    FIELD_IDS,
    INTERNAL_CLIENT,
    NOW,
    SITE_ID,
    FakeBC,
    FakeFrontu,
    FakeSyncRepository,
    vehicle,
)

T0 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
SHIP_TO = BCODataShipToAddress(Customer_No="C1", Code="VIL", Name="Vilnius site", City="Vilnius")


def _pipeline(
    bc: FakeBC, frontu: FakeFrontu, repo: FakeSyncRepository, *, dry_run: bool = False
) -> VehicleSyncPipeline:
    return VehicleSyncPipeline(
        bc, frontu, repo, INTERNAL_CLIENT, dry_run=dry_run, clock=lambda: NOW
    )


def _repo() -> FakeSyncRepository:
    return FakeSyncRepository(cursor=T0 - timedelta(hours=1))


def _seed_fingerprint(repo: FakeSyncRepository, source: BCODataVehicle) -> None:
    expected = build_workspace(source, None, FIELD_IDS, INTERNAL_CLIENT)
    repo.fingerprints[source.SerialNo] = payload_hash(expected)


@pytest.mark.asyncio
async def test_new_vehicle_is_upserted_fingerprinted_and_audited() -> None:
    repo, frontu = _repo(), FakeFrontu()

    result = await _pipeline(FakeBC([[vehicle("SN-1", T0)]]), frontu, repo).run()

    assert result is not None and result.processed == 1
    assert repo.fingerprints == {"SN-1": payload_hash(frontu.pushed[0])}
    entry = repo.audit[0]
    assert (entry.entity_key, entry.action, entry.status_code) == ("SN-1", "UPSERT", 201)
    assert entry.payload is not None and entry.payload["systemId"] == "SN-1"


@pytest.mark.asyncio
async def test_unchanged_vehicle_is_skipped_without_calling_frontu() -> None:
    repo, frontu, source = _repo(), FakeFrontu(), vehicle("SN-1", T0)
    _seed_fingerprint(repo, source)

    result = await _pipeline(FakeBC([[source]]), frontu, repo).run()

    assert frontu.pushed == []
    assert [e.action for e in repo.audit] == ["SKIPPED"]
    assert result is not None and result.skipped == 1


@pytest.mark.asyncio
async def test_blocked_vehicle_is_archived_and_fingerprint_dropped() -> None:
    repo, frontu = _repo(), FakeFrontu()
    _seed_fingerprint(repo, vehicle("SN-1", T0))

    await _pipeline(FakeBC([[vehicle("SN-1", T0, blocked=True)]]), frontu, repo).run()

    assert [p.deleted for p in frontu.pushed] == [True]
    assert [e.action for e in repo.audit] == ["ARCHIVE"]
    assert "SN-1" not in repo.fingerprints


@pytest.mark.asyncio
async def test_unblocked_vehicle_is_resent_after_archive() -> None:
    repo, frontu = _repo(), FakeFrontu()
    bc = FakeBC([[vehicle("SN-1", T0, blocked=True)]])
    pipeline = _pipeline(bc, frontu, repo)

    await pipeline.run()
    bc.pages = [[vehicle("SN-1", T0 + timedelta(minutes=1))]]
    await pipeline.run()

    assert [p.deleted for p in frontu.pushed] == [True, False]
    assert [e.action for e in repo.audit] == ["ARCHIVE", "UPSERT"]
    assert repo.fingerprints == {"SN-1": payload_hash(frontu.pushed[1])}


@pytest.mark.asyncio
async def test_ship_to_enriches_workspace_and_is_cached_per_run() -> None:
    frontu = FakeFrontu()
    page = [
        vehicle("SN-1", T0, customer="C1", address_code="VIL"),
        vehicle("SN-2", T0, customer="C1", address_code="VIL"),
        vehicle("SN-3", T0, customer="C1"),
    ]
    bc = FakeBC([page], ship_to={("C1", "VIL"): SHIP_TO})

    await _pipeline(bc, frontu, _repo()).run()

    assert bc.ship_to_calls == [("C1", "VIL")]
    assert frontu.pushed[0].city == "Vilnius"
    assert frontu.pushed[0].customValues == {SITE_ID: "Vilnius site"}
    assert frontu.pushed[2].city is None


@pytest.mark.asyncio
async def test_frontu_rejection_is_audited_without_fingerprint() -> None:
    repo = _repo()
    frontu = FakeFrontu(failures={"SN-1": 422})

    result = await _pipeline(
        FakeBC([[vehicle("SN-1", T0), vehicle("SN-2", T0)]]), frontu, repo
    ).run()

    failed = repo.audit[0]
    assert (failed.action, failed.status_code) == ("FAILED", 422)
    assert failed.error_message is not None and "rejected" in failed.error_message
    assert list(repo.fingerprints) == ["SN-2"]
    assert result is not None and (result.failed, result.processed) == (1, 1)
    assert repo.statuses[-1] == "IDLE"


@pytest.mark.asyncio
async def test_non_200_201_success_is_not_fingerprinted() -> None:
    repo = _repo()

    await _pipeline(FakeBC([[vehicle("SN-1", T0)]]), FakeFrontu(status_code=202), repo).run()

    assert [(e.action, e.status_code) for e in repo.audit] == [("FAILED", 202)]
    assert repo.fingerprints == {}


@pytest.mark.asyncio
async def test_ship_to_lookup_failure_fails_only_that_record() -> None:
    repo, frontu = _repo(), FakeFrontu()
    page = [vehicle("SN-1", T0, customer="C1", address_code="BAD"), vehicle("SN-2", T0)]
    bc = FakeBC([page], failing_ship_to=frozenset({("C1", "BAD")}))

    await _pipeline(bc, frontu, repo).run()

    assert [(e.entity_key, e.action) for e in repo.audit] == [
        ("SN-1", "FAILED"),
        ("SN-2", "UPSERT"),
    ]
    assert [p.systemId for p in frontu.pushed] == ["SN-2"]


@pytest.mark.parametrize(
    ("blocked", "action"), [(False, "DRY_RUN_UPSERT"), (True, "DRY_RUN_ARCHIVE")]
)
@pytest.mark.asyncio
async def test_dry_run_audits_intended_payload_without_any_mutation(
    blocked: bool, action: str
) -> None:
    repo, frontu = _repo(), FakeFrontu()
    repo.fingerprints["SN-1"] = "stale-hash"
    cursor_before = repo.cursor
    bc = FakeBC([[vehicle("SN-1", T0, blocked=blocked)]])

    result = await _pipeline(bc, frontu, repo, dry_run=True).run()

    assert frontu.pushed == []
    entry = repo.audit[0]
    assert entry.action == action
    assert entry.payload is not None and entry.payload["deleted"] is blocked
    assert repo.fingerprints == {"SN-1": "stale-hash"}
    assert repo.cursor == cursor_before
    assert result is not None and (result.planned, result.processed) == (1, 0)


@pytest.mark.asyncio
async def test_dry_run_still_skips_unchanged_vehicles() -> None:
    repo, frontu, source = _repo(), FakeFrontu(), vehicle("SN-1", T0)
    _seed_fingerprint(repo, source)

    await _pipeline(FakeBC([[source]]), frontu, repo, dry_run=True).run()

    assert [e.action for e in repo.audit] == ["SKIPPED"]
    assert frontu.pushed == []
