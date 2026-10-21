"""Shared plumbing for the read-only operations reports: Frontu rows in, Excel-friendly CSV out."""

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

import httpx
from pydantic import ConfigDict, JsonValue, ValidationError

from src.adapters.frontu_client import FrontuClient
from src.config import get_settings
from src.schemas.frontu_models import FrontuWorkspaceSummary

REPORTS_DIR = Path(__file__).resolve().parents[1] / "data" / "reports"
HTTP_TIMEOUT_SECONDS = 30.0


class ReportRow(FrontuWorkspaceSummary):
    """Identity fields plus the human-facing columns of the reports.

    `client` stays raw JSON so an unexpected shape never drops a row from a report.
    """

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    title: str | None = None
    updated: str | None = None
    created: str | None = None
    client: JsonValue = None

    @property
    def changed_at(self) -> str:
        return self.updated or self.created or ""

    def client_field(self, name: str) -> str:
        value = self.client.get(name) if isinstance(self.client, dict) else None
        return "" if value is None else str(value)


def parse_report_rows(raw_rows: Iterable[JsonValue]) -> tuple[dict[str, ReportRow], int]:
    """Rows keyed by Frontu guid, plus the number of rows that could not be parsed."""
    rows: dict[str, ReportRow] = {}
    skipped = 0
    for raw in raw_rows:
        try:
            row = ReportRow.model_validate(raw)
        except ValidationError:
            skipped += 1
            continue
        rows[row.id] = row
    return rows, skipped


def write_csv(path: Path, columns: Sequence[str], lines: Iterable[Sequence[str]]) -> None:
    """CSV with a UTF-8 BOM so Excel shows Lithuanian characters correctly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(lines)


async def fetch_frontu_rows(http: httpx.AsyncClient) -> list[JsonValue]:
    """Every raw `object` workspace row via the paced, retrying adapter (GET only)."""
    settings = get_settings()
    client = FrontuClient(http, settings.active_frontu_base_url, settings.active_frontu_api_key)
    raw: list[JsonValue] = []
    async for _page, rows in client.iter_workspace_pages():
        raw.extend(rows)
    return raw
