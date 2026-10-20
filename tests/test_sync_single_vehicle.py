"""Targeted single-vehicle sync: canary push, dry-run, shared lock and cursor isolation."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.pipelines.single_vehicle import SingleVehicleSync
from src.pipelines.sync_vehicles import VehicleSyncPipeline
from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from src.schemas.bc_vehicle import BCODataShipToAddress
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
CURSOR = T0 - timedelta(hours=1)


def _single(
    bc: FakeBC,
    frontu: FakeFrontu,
    repo: FakeSyncRepository,
    *,
    dry_run: bool = True,
    lock: asyncio.Lock | None = None,
) -> SingleVehicleSync:
    return SingleVehicleSync(
        bc,
        frontu,
        repo,
        INTERNAL_CLIENT,
        lock=lock or asyncio.Lock(),
        dry_run=dry_run,
        clock=lambda: NOW,
    )


def _assert_state_untouched(repo: FakeSyncRepository) -> None:
    assert repo.cursor == CURSOR
    assert repo.statuses == []
    assert repo.heartbeats == 0


@pytest.mark.asyncio
async def test_force_live_overrides_dry_run_and_pushes_even_when_unchanged() -> None:
    source = vehicle("VSN1", T0, customer="C1", address_code="VIL")
    ship_to = BCODataShipToAddress(Customer_No="C1", Code="VIL", Name="Vilnius site")
    bc = FakeBC([[source]], ship_to={("C1", "VIL"): ship_to})
    repo, frontu = FakeSyncRepository(cursor=CURSOR), FakeFrontu()
    expected = build_workspace(source, ship_to, FIELD_IDS, INTERNAL_CLIENT)
    repo.fingerprints["VSN1"] = payload_hash(expected)

    result = await _single(bc, frontu, repo).sync_single_vehicle("VSN1", force_live=True)

    assert (result.outcome, result.action, result.status_code, result.dry_run) == (
        "SYNCED",
        "SINGLE_UPSERT",
        201,
        False,
    )
    assert frontu.pushed == [expected]
    assert frontu.pushed[0].customValues == {SITE_ID: "Vilnius site"}
    assert repo.fingerprints == {"VSN1": payload_hash(expected)}
    assert [(e.entity_key, e.action, e.status_code) for e in repo.audit] == [
        ("VSN1", "SINGLE_UPSERT", 201)
    ]
    _assert_state_untouched(repo)


@pytest.mark.asyncio
async def test_live_config_without_force_flag_also_pushes() -> None:
    repo, frontu = FakeSyncRepository(cursor=CURSOR), FakeFrontu()

    single = _single(FakeBC([[vehicle("VSN1", T0)]]), frontu, repo, dry_run=False)
    result = await single.sync_single_vehicle("VSN1")

    assert result.outcome == "SYNCED"
    assert len(frontu.pushed) == 1


@pytest.mark.asyncio
async def test_blocked_vehicle_live_is_archived_and_fingerprint_dropped() -> None:
    repo, frontu = FakeSyncRepository(cursor=CURSOR), FakeFrontu()
    repo.fingerprints["VSN1"] = "old-hash"
    bc = FakeBC([[vehicle("VSN1", T0, blocked=True)]])

    result = await _single(bc, frontu, repo).sync_single_vehicle("VSN1", force_live=True)

    assert (result.outcome, result.action) == ("SYNCED", "SINGLE_ARCHIVE")
    assert frontu.pushed[0].deleted is True
    assert repo.fingerprints == {}
    _assert_state_untouched(repo)


@pytest.mark.parametrize(
    ("blocked", "action"), [(False, "DRY_RUN_SINGLE_UPSERT"), (True, "DRY_RUN_SINGLE_ARCHIVE")]
)
@pytest.mark.asyncio
async def test_default_is_dry_run_without_any_mutation(blocked: bool, action: str) -> None:
    repo, frontu = FakeSyncRepository(cursor=CURSOR), FakeFrontu()
    repo.fingerprints["VSN1"] = "old-hash"
    bc = FakeBC([[vehicle("VSN1", T0, blocked=blocked)]])

    result = await _single(bc, frontu, repo).sync_single_vehicle("VSN1")

    assert (result.outcome, result.action, result.dry_run) == ("DRY_RUN", action, True)
    assert frontu.pushed == []
    assert repo.fingerprints == {"VSN1": "old-hash"}
    entry = repo.audit[0]
    assert entry.action == action and entry.payload is not None
    _assert_state_untouched(repo)


@pytest.mark.asyncio
async def test_unknown_serial_returns_not_found_without_side_effects() -> None:
    repo, frontu, bc = FakeSyncRepository(cursor=CURSOR), FakeFrontu(), FakeBC([])

    result = await _single(bc, frontu, repo).sync_single_vehicle("NOPE", force_live=True)

    assert result.outcome == "NOT_FOUND"
    assert bc.vehicle_calls == ["NOPE"]
    assert (frontu.pushed, repo.audit) == ([], [])
    _assert_state_untouched(repo)


@pytest.mark.parametrize(
    ("frontu", "failing", "status"),
    [
        (FakeFrontu(failures={"VSN1": 422}), frozenset[str](), 422),
        (FakeFrontu(status_code=202), frozenset[str](), 202),
        (FakeFrontu(), frozenset({"VSN1"}), 503),
    ],
    ids=["frontu-rejects", "unconfirmed-2xx", "bc-down"],
)
@pytest.mark.asyncio
async def test_failures_are_audited_and_reported_not_raised(
    frontu: FakeFrontu, failing: frozenset[str], status: int
) -> None:
    repo = FakeSyncRepository(cursor=CURSOR)
    bc = FakeBC([[vehicle("VSN1", T0)]], failing_vehicles=failing)

    result = await _single(bc, frontu, repo).sync_single_vehicle("VSN1", force_live=True)

    assert (result.outcome, result.status_code) == ("FAILED", status)
    assert result.error
    assert [(e.action, e.status_code) for e in repo.audit] == [("FAILED", status)]
    assert repo.fingerprints == {}
    _assert_state_untouched(repo)


@pytest.mark.asyncio
async def test_shared_lock_makes_single_and_batch_mutually_exclusive() -> None:
    lock = asyncio.Lock()
    repo, frontu = FakeSyncRepository(cursor=CURSOR), FakeFrontu()
    bc = FakeBC([[vehicle("VSN1", T0)]])
    batch = VehicleSyncPipeline(bc, frontu, repo, INTERNAL_CLIENT, lock=lock, clock=lambda: NOW)
    single = _single(bc, frontu, repo, lock=lock)

    running_batch = asyncio.create_task(batch.run())
    await asyncio.sleep(0)
    busy = await single.sync_single_vehicle("VSN1", force_live=True)
    await running_batch

    assert (busy.outcome, bc.vehicle_calls) == ("BUSY", [])

    running_single = asyncio.create_task(single.sync_single_vehicle("VSN1", force_live=True))
    await asyncio.sleep(0)
    assert await batch.run() is None
    assert (await running_single).outcome == "SYNCED"
