"""Read-only report of BC vehicles that match an unmanaged legacy Frontu workspace (SPEC 10).

Each line pairs an active BC vehicle with the single unmanaged legacy workspace it matches
(VIN first, then SerialNo). The sync upserts by `systemId` only, so unless operations set
`systemId` on the legacy workspace in the Frontu UI before the first live sync of that
vehicle, the sync creates a second workspace for it.

The script only issues GET requests through the BC and Frontu adapters and writes a local
CSV; it never writes to Frontu, BC or the sync database.

Usage (from the project root): python -m scripts.export_legacy_candidates
"""

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx
from pydantic import JsonValue

from scripts.report_io import (
    REPORTS_DIR,
    fetch_frontu_rows,
    parse_report_rows,
    write_csv,
)
from src.adapters.bc_client import BCClient
from src.config import get_settings
from src.main import select_connection
from src.pipelines.identity_resolver import LegacyIndex
from src.schemas.bc_vehicle import BCODataVehicle

REPORT_PATH = REPORTS_DIR / "legacy_adoption_candidates.csv"
# Delta query start early enough to return the whole VehicleCard table.
ALL_VEHICLES_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
# Full-table VehicleCard pages from BC on-prem can take well over 30 s each.
HTTP_TIMEOUT_SECONDS = 120.0
COLUMNS = (
    "bc_serial_no",
    "bc_vin",
    "bc_customer_no",
    "matched_on",
    "frontu_guid",
    "frontu_serial_number",
    "frontu_title",
    "frontu_client",
    "frontu_client_system_id",
    "action",
)
CandidateLine = tuple[str, str, str, str, str, str, str, str, str, str]


def build_report(
    vehicles: Iterable[BCODataVehicle], raw_rows: Iterable[JsonValue]
) -> tuple[list[CandidateLine], int]:
    """Candidate lines in SerialNo order, plus the number of unparsed Frontu rows."""
    rows, skipped = parse_report_rows(raw_rows)
    index = LegacyIndex(rows.values())
    lines: list[CandidateLine] = []
    for vehicle in sorted(vehicles, key=lambda v: v.SerialNo):
        decision = index.resolve(vehicle)
        if decision.kind != "LEGACY_MATCH" or decision.workspace_id is None:
            continue
        legacy = rows[decision.workspace_id]
        serial = vehicle.SerialNo.strip()
        lines.append(
            (
                serial,
                vehicle.VIN or "",
                vehicle.CustomerNo or "",
                decision.matched_on or "",
                legacy.id,
                legacy.serialNumber or "",
                legacy.title or "",
                legacy.client_field("title"),
                legacy.client_field("systemId"),
                f"Set systemId to {serial} in Frontu, then run the single-vehicle sync",
            )
        )
    return lines, skipped


async def fetch_bc_vehicles(http: httpx.AsyncClient) -> list[BCODataVehicle]:
    client = BCClient(http, select_connection(get_settings()))
    vehicles: list[BCODataVehicle] = []
    async for page in client.fetch_vehicles(ALL_VEHICLES_SINCE):
        vehicles.extend(page)
    return vehicles


async def main() -> None:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
        raw = await fetch_frontu_rows(http)
        vehicles = await fetch_bc_vehicles(http)
    lines, skipped = build_report(vehicles, raw)
    write_csv(REPORT_PATH, COLUMNS, lines)
    active = sum(1 for v in vehicles if not v.Blocked)
    print(f"Frontu rows read: {len(raw)} (unparsed: {skipped})")
    print(f"BC vehicles read: {len(vehicles)} (active: {active})")
    print(f"Manual remediation candidates: {len(lines)}")
    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
