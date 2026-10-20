"""Legacy adoption candidates report: same decisions as the pipeline resolver (no network)."""

from datetime import UTC, datetime

from pydantic import JsonValue

from scripts.export_legacy_candidates import COLUMNS, build_report
from src.schemas.bc_vehicle import BCODataVehicle

T0 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def _vehicle(serial: str, vin: str | None, *, blocked: bool = False) -> BCODataVehicle:
    return BCODataVehicle(
        SerialNo=serial, VIN=vin, CustomerNo="2086", Blocked=blocked, SystemModifiedAt=T0
    )


def _row(guid: str, serial: str, system_id: str | None = None) -> dict[str, JsonValue]:
    return {
        "guid": guid,
        "systemId": system_id,
        "serialNumber": serial,
        "type": "object",
        "title": f"Title {guid}",
        "client": {"title": "Alwark, UAB", "systemId": "2086"},
    }


RAW: list[JsonValue] = [
    _row("g-vin", "w4x131h06549"),
    _row("g-serial", "SN-200"),
    _row("g-dup-a", "VIN-DUP"),
    _row("g-dup-b", "VIN-DUP"),
    _row("g-managed", "VIN-M", system_id="SN-300"),
    _row("g-blocked", "VIN-B"),
    {"title": "no identifiers"},
]
VEHICLES = [
    _vehicle("12100", "W4X131H06549"),
    _vehicle("SN-200", None),
    _vehicle("SN-DUP", "VIN-DUP"),
    _vehicle("SN-300", "VIN-M"),
    _vehicle("SN-400", "VIN-B", blocked=True),
    _vehicle("SN-500", "NO-MATCH"),
]


def test_report_lists_unique_legacy_matches_only() -> None:
    lines, skipped = build_report(VEHICLES, RAW)

    assert skipped == 1
    assert [(line[0], line[3], line[4]) for line in lines] == [
        ("12100", "VIN", "g-vin"),
        ("SN-200", "SerialNo", "g-serial"),
    ]
    assert len(COLUMNS) == len(lines[0])
    assert lines[0] == (
        "12100",
        "W4X131H06549",
        "2086",
        "VIN",
        "g-vin",
        "w4x131h06549",
        "Title g-vin",
        "Alwark, UAB",
        "2086",
        "Set systemId to 12100 in Frontu, then run the single-vehicle sync",
    )
