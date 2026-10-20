"""Read-only report of Frontu legacy workspaces that share a serial number (SPEC section 10).

These are the groups the identity resolver refuses to adopt (`FAILED_AMBIGUOUS_MATCH`).
The script only issues paced GET /workspaces calls through the Frontu adapter and writes
a local CSV; it never writes to Frontu, BC or the sync database.

Usage (from the project root): python -m scripts.export_ambiguous_frontu
"""

import asyncio
import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

import httpx
from pydantic import ConfigDict, JsonValue, ValidationError

from src.adapters.frontu_client import FrontuClient
from src.config import get_settings
from src.pipelines.identity_resolver import LegacyIndex
from src.schemas.frontu_models import FrontuWorkspaceSummary

REPORT_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "reports" / "frontu_ambiguous_duplicates.csv"
)
COLUMNS = ("serial_number", "frontu_guid", "title", "updated_at")
HTTP_TIMEOUT_SECONDS = 30.0
ReportLine = tuple[str, str, str, str]


class ReportRow(FrontuWorkspaceSummary):
    """Identity fields plus the human-facing columns of the report."""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    title: str | None = None
    updated: str | None = None
    created: str | None = None

    @property
    def changed_at(self) -> str:
        return self.updated or self.created or ""


def build_report(raw_rows: Iterable[JsonValue]) -> tuple[list[ReportLine], int]:
    """Report lines sorted by serial key, then oldest change first; plus unparsed row count."""
    rows: dict[str, ReportRow] = {}
    skipped = 0
    for raw in raw_rows:
        try:
            row = ReportRow.model_validate(raw)
        except ValidationError:
            skipped += 1
            continue
        rows[row.id] = row
    lines: list[ReportLine] = []
    for key, guids in LegacyIndex(rows.values()).ambiguous_groups().items():
        members = sorted((rows[guid] for guid in guids), key=lambda r: (r.changed_at, r.id))
        lines.extend((key, r.id, r.title or "", r.changed_at) for r in members)
    return lines, skipped


def write_report(lines: Sequence[ReportLine], path: Path) -> None:
    """CSV with a UTF-8 BOM so Excel shows Lithuanian characters correctly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(lines)


async def fetch_raw_rows() -> list[JsonValue]:
    settings = get_settings()
    raw: list[JsonValue] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
        client = FrontuClient(http, settings.frontu_subdomain, settings.frontu_api_key)
        async for _page, rows in client.iter_workspace_pages():
            raw.extend(rows)
    return raw


async def main() -> None:
    raw = await fetch_raw_rows()
    lines, skipped = build_report(raw)
    write_report(lines, REPORT_PATH)
    groups = len({line[0] for line in lines})
    print(f"Frontu rows read: {len(raw)} (unparsed: {skipped})")
    print(f"Ambiguous serial groups: {groups}, workspaces: {len(lines)}")
    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
