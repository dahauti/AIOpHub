"""Read-only report of Frontu legacy workspaces that share a serial number (SPEC section 10).

These are the groups the identity resolver refuses to adopt (`FAILED_AMBIGUOUS_MATCH`).
The script only issues paced GET /workspaces calls through the Frontu adapter and writes
a local CSV; it never writes to Frontu, BC or the sync database.

Usage (from the project root): python -m scripts.export_ambiguous_frontu
"""

import asyncio
from collections.abc import Iterable, Sequence
from pathlib import Path

import httpx
from pydantic import JsonValue

from scripts.report_io import (
    HTTP_TIMEOUT_SECONDS,
    REPORTS_DIR,
    fetch_frontu_rows,
    parse_report_rows,
    write_csv,
)
from src.pipelines.identity_resolver import LegacyIndex

REPORT_PATH = REPORTS_DIR / "frontu_ambiguous_duplicates.csv"
COLUMNS = ("serial_number", "frontu_guid", "title", "updated_at")
ReportLine = tuple[str, str, str, str]


def build_report(raw_rows: Iterable[JsonValue]) -> tuple[list[ReportLine], int]:
    """Report lines sorted by serial key, then oldest change first; plus unparsed row count."""
    rows, skipped = parse_report_rows(raw_rows)
    lines: list[ReportLine] = []
    for key, guids in LegacyIndex(rows.values()).ambiguous_groups().items():
        members = sorted((rows[guid] for guid in guids), key=lambda r: (r.changed_at, r.id))
        lines.extend((key, r.id, r.title or "", r.changed_at) for r in members)
    return lines, skipped


def write_report(lines: Sequence[ReportLine], path: Path) -> None:
    write_csv(path, COLUMNS, lines)


async def main() -> None:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
        raw = await fetch_frontu_rows(http)
    lines, skipped = build_report(raw)
    write_report(lines, REPORT_PATH)
    groups = len({line[0] for line in lines})
    print(f"Frontu rows read: {len(raw)} (unparsed: {skipped})")
    print(f"Ambiguous serial groups: {groups}, workspaces: {len(lines)}")
    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
