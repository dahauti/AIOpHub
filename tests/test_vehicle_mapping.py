"""BC vehicle -> Frontu workspace mapping rules and payload fingerprinting."""

import hashlib
from datetime import UTC, datetime

import pytest

from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from src.schemas.bc_vehicle import BCODataShipToAddress
from src.schemas.warranty import ResolvedWarranty
from tests.pipeline_fakes import FIELD_IDS, INTERNAL_CLIENT, REG_ID, SITE_ID, vehicle

T0 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
SHIP_TO = BCODataShipToAddress(
    Customer_No="C1",
    Code="VIL",
    Name="Vilniaus padalinys",
    Address="Gedimino 1",
    City="Vilnius",
    Post_Code="LT-01103",
)


@pytest.mark.parametrize(
    ("make", "model", "expected"),
    [
        ("VOLVO", "FH16", "VOLVO FH16"),
        (None, "FH16", "FH16"),
        ("VOLVO", None, "VOLVO"),
        (None, None, "SN-1"),
        ("", "  ", "SN-1"),
    ],
)
def test_title_joins_make_and_model_with_serial_fallback(
    make: str | None, model: str | None, expected: str
) -> None:
    workspace = build_workspace(vehicle("SN-1", T0, make=make, model=model), None, FIELD_IDS, "X")

    assert workspace.title == expected


def test_client_falls_back_to_internal_company() -> None:
    own = build_workspace(vehicle("SN-1", T0, customer="C1"), None, FIELD_IDS, INTERNAL_CLIENT)
    internal = build_workspace(vehicle("SN-2", T0), None, FIELD_IDS, INTERNAL_CLIENT)

    assert own.client == "C1"
    assert internal.client == INTERNAL_CLIENT


def test_ship_to_supplies_address_and_site_custom_value() -> None:
    source = vehicle("SN-1", T0, customer="C1", address_code="VIL", registration="ABC123")

    workspace = build_workspace(source, SHIP_TO, FIELD_IDS, INTERNAL_CLIENT)

    assert (workspace.address, workspace.city, workspace.zipCode) == (
        "Gedimino 1",
        "Vilnius",
        "LT-01103",
    )
    assert workspace.customValues == {REG_ID: "ABC123", SITE_ID: "Vilniaus padalinys"}
    assert workspace.systemId == "SN-1"
    assert workspace.type == "object"


def test_without_ship_to_only_registration_is_mapped() -> None:
    workspace = build_workspace(vehicle("SN-1", T0, registration="ABC123"), None, FIELD_IDS, "X")

    assert workspace.address is None
    assert workspace.customValues == {REG_ID: "ABC123"}


def test_deleted_mirrors_blocked_and_vin_maps_to_serial_number() -> None:
    source = vehicle("SN-1", T0, blocked=True).model_copy(update={"VIN": "YV2RT40A0KB123456"})

    workspace = build_workspace(source, None, FIELD_IDS, "X")

    assert workspace.deleted is True
    assert workspace.serialNumber == "YV2RT40A0KB123456"


def test_payload_hash_is_sha256_of_json_without_deleted() -> None:
    active = build_workspace(vehicle("SN-1", T0), None, FIELD_IDS, "X")
    blocked = active.model_copy(update={"deleted": True})
    renamed = active.model_copy(update={"title": "Other"})

    expected = hashlib.sha256(active.model_dump_json(exclude={"deleted"}).encode()).hexdigest()
    assert payload_hash(active) == expected
    assert len(expected) == 64
    assert payload_hash(blocked) == payload_hash(active)
    assert payload_hash(renamed) != payload_hash(active)


def test_resolved_warranty_maps_to_top_level_fields_and_changes_hash() -> None:
    resolved = ResolvedWarranty(
        start_date="2025-01-15",
        end_date="2026-01-15",
        extended_end_date="2028-01-15",
        indicator_limit=4000,
    )
    source = vehicle("SN-1", T0)

    plain = build_workspace(source, None, FIELD_IDS, INTERNAL_CLIENT)
    enriched = build_workspace(source, None, FIELD_IDS, INTERNAL_CLIENT, resolved)

    assert (plain.warrantyStartDate, plain.warrantyIndicatorLimit) == (None, None)
    assert enriched.warrantyStartDate == "2025-01-15"
    assert enriched.warrantyEndDate == "2026-01-15"
    assert enriched.extendedWarrantyEndDate == "2028-01-15"
    assert enriched.warrantyIndicatorLimit == 4000
    assert payload_hash(enriched) != payload_hash(plain)
