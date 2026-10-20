"""Ambiguous-duplicates report: grouping, parsing and CSV output (no network)."""

import csv
from pathlib import Path

from pydantic import JsonValue

from scripts.export_ambiguous_frontu import COLUMNS, build_report, write_report


def _row(guid: str, serial: str | None, **extra: JsonValue) -> dict[str, JsonValue]:
    row: dict[str, JsonValue] = {"guid": guid, "systemId": None, "serialNumber": serial}
    row.update({"type": "object", "title": f"Title {guid}"}, **extra)
    return row


RAW: list[JsonValue] = [
    _row("g2", "vin-1", updated="2025-03-01T10:00:00+00:00"),
    _row("g1", "VIN-1 ", updated="2024-01-01T10:00:00+00:00", title="Krautuvas Ž"),
    _row("g3", "VIN-1", systemId="SN-1"),
    _row("g4", "VIN-2"),
    _row("g5", "AB-7", created="2023-05-05", updated=None),
    _row("g6", "ab-7", title=None),
    {"serialNumber": "VIN-1"},
    "garbage",
]


def test_report_lists_only_unmanaged_groups_sorted_by_key_then_date() -> None:
    lines, skipped = build_report(RAW)

    assert skipped == 2
    assert lines == [
        ("AB-7", "g6", "", ""),
        ("AB-7", "g5", "Title g5", "2023-05-05"),
        ("VIN-1", "g1", "Krautuvas Ž", "2024-01-01T10:00:00+00:00"),
        ("VIN-1", "g2", "Title g2", "2025-03-01T10:00:00+00:00"),
    ]


def test_csv_has_header_and_excel_friendly_utf8(tmp_path: Path) -> None:
    target = tmp_path / "reports" / "dupes.csv"

    write_report([("VIN-1", "g1", "Krautuvas Ž", "2024-01-01")], target)

    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with target.open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == [
            list(COLUMNS),
            ["VIN-1", "g1", "Krautuvas Ž", "2024-01-01"],
        ]
