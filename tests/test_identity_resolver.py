"""Legacy workspace identity resolution: adopt, upsert or fail closed (SPEC section 10)."""

from datetime import UTC, datetime

import pytest

from src.pipelines.identity_resolver import IdentityDecision, LegacyIndex
from src.schemas.bc_vehicle import BCODataVehicle
from src.schemas.frontu_models import FrontuWorkspaceSummary
from tests.pipeline_fakes import workspace

T0 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
UPSERT = IdentityDecision("UPSERT")


def _vehicle(serial: str, vin: str | None = None, *, blocked: bool = False) -> BCODataVehicle:
    return BCODataVehicle(SerialNo=serial, VIN=vin, Blocked=blocked, SystemModifiedAt=T0)


def _index(*workspaces: FrontuWorkspaceSummary) -> LegacyIndex:
    return LegacyIndex(workspaces)


def test_no_match_upserts() -> None:
    assert _index(workspace("w1", "OTHER")).resolve(_vehicle("SN-1", "VIN-1")) == UPSERT


def test_unique_vin_match_is_adopted_with_normalisation() -> None:
    index = _index(workspace("w1", "  vin-1 "))

    assert index.resolve(_vehicle("SN-1", "VIN-1")) == IdentityDecision("ADOPT", "w1")


def test_serial_no_is_the_fallback_key() -> None:
    index = _index(workspace("w2", "sn-1"))

    assert index.resolve(_vehicle(" SN-1", "VIN-1")) == IdentityDecision("ADOPT", "w2")


def test_vin_match_takes_priority_over_serial_no_match() -> None:
    index = _index(workspace("by-serial", "SN-1"), workspace("by-vin", "VIN-1"))

    assert index.resolve(_vehicle("SN-1", "VIN-1")) == IdentityDecision("ADOPT", "by-vin")


@pytest.mark.parametrize(("vin", "serial"), [("VIN-1", "SN-1"), (None, "SN-1")])
def test_duplicate_matches_fail_closed_without_fallback(vin: str | None, serial: str) -> None:
    key = vin or serial
    index = _index(workspace("a", key), workspace("b", key.lower()), workspace("c", serial))

    decision = index.resolve(_vehicle(serial, vin))

    assert decision.kind == "AMBIGUOUS"
    assert set(decision.candidates) >= {"a", "b"}
    assert decision.workspace_id is None


def test_already_managed_vehicle_is_upserted_even_with_legacy_match() -> None:
    index = _index(workspace("managed", "VIN-1", system_id="SN-1"), workspace("w1", "VIN-1"))

    assert index.resolve(_vehicle("SN-1", "VIN-1")) == UPSERT


@pytest.mark.parametrize("system_id", [None, "", "   "])
def test_blank_system_id_counts_as_unmanaged(system_id: str | None) -> None:
    index = _index(workspace("w1", "VIN-1", system_id=system_id))

    assert index.resolve(_vehicle("SN-1", "VIN-1")).kind == "ADOPT"


def test_workspace_managed_by_another_vehicle_is_never_adopted() -> None:
    index = _index(workspace("w1", "VIN-1", system_id="SN-OTHER"))

    assert index.resolve(_vehicle("SN-1", "VIN-1")) == UPSERT


def test_blocked_vehicle_is_never_adopted() -> None:
    index = _index(workspace("w1", "VIN-1"))

    assert index.resolve(_vehicle("SN-1", "VIN-1", blocked=True)) == UPSERT


def test_non_object_and_serial_less_workspaces_are_ignored() -> None:
    index = _index(workspace("w1", "VIN-1", kind="client"), workspace("w2", None))

    assert index.resolve(_vehicle("SN-1", "VIN-1")) == UPSERT


def test_claimed_workspace_is_reserved_for_its_vehicle() -> None:
    index = _index(workspace("w1", "SHARED"))

    first = index.resolve(_vehicle("SN-1", "SHARED"))
    other = index.resolve(_vehicle("SN-2", "SHARED"))
    again = index.resolve(_vehicle("SN-1", "SHARED"))

    assert first == again == IdentityDecision("ADOPT", "w1")
    assert other == UPSERT


def test_ambiguous_groups_lists_shared_serial_keys_only() -> None:
    index = _index(
        workspace("a", "vin-1"),
        workspace("b", " VIN-1"),
        workspace("c", "VIN-2"),
        workspace("d", "VIN-1", system_id="SN-9"),
    )

    assert index.ambiguous_groups() == {"VIN-1": ["a", "b"]}
