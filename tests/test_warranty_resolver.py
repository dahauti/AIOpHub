"""Warranty resolver: BC date formulas and VehicleWarrList hierarchy rules (SPEC section 9)."""

from datetime import date

import pytest

from src.pipelines.warranty_resolver import ResolvedWarranty, apply_date_formula, resolve_warranty
from tests.pipeline_fakes import warranty

START = date(2025, 1, 15)


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("1Y", date(2026, 1, 15)),
        ("3M", date(2025, 4, 15)),
        ("6M", date(2025, 7, 15)),
        ("10D", date(2025, 1, 25)),
        ("0Y", START),
        ("1y", date(2026, 1, 15)),
        ("1Y6M", date(2026, 7, 15)),
        ("1Y+3M", date(2026, 4, 15)),
        ("2Y+6M", date(2027, 7, 15)),
        ("<1Y>", date(2026, 1, 15)),
    ],
)
def test_formula_is_added_to_starting_date(formula: str, expected: date) -> None:
    assert apply_date_formula(START, formula) == expected


@pytest.mark.parametrize(
    ("start", "formula", "expected"),
    [
        (date(2024, 2, 29), "1Y", date(2025, 2, 28)),
        (date(2024, 2, 29), "4Y", date(2028, 2, 29)),
        (date(2025, 1, 31), "1M", date(2025, 2, 28)),
        (date(2024, 1, 31), "1M", date(2024, 2, 29)),
        (date(2025, 11, 30), "3M", date(2026, 2, 28)),
        (date(2025, 1, 31), "1M1M", date(2025, 3, 28)),
    ],
)
def test_month_end_is_clamped_on_each_step(start: date, formula: str, expected: date) -> None:
    assert apply_date_formula(start, formula) == expected


@pytest.mark.parametrize(
    "formula", [None, "", "  ", "1W", "CM", "CM+1D", "-1Y", "1Y-1D", "Y", "9999Y"]
)
def test_unsupported_formula_yields_none(formula: str | None) -> None:
    assert apply_date_formula(START, formula) is None


def test_no_records_resolve_to_empty_warranty() -> None:
    assert resolve_warranty([]) == ResolvedWarranty()


def test_single_standard_warranty() -> None:
    resolved = resolve_warranty([warranty("SN-1", formula="2Y", run=1500)])

    assert resolved == ResolvedWarranty(
        start_date="2025-01-15", end_date="2027-01-15", indicator_limit=1500
    )


def test_standard_and_extended_warranties_resolve_separately() -> None:
    records = [
        warranty("SN-1", code="EXTENDED_FACTORY", start="2026-01-15", formula="2Y", run=4000.7),
        warranty("SN-1", code="STANDART_FACTORY", start="2025-01-15", formula="1Y", run=2000),
    ]

    resolved = resolve_warranty(records)

    assert resolved.start_date == "2025-01-15"
    assert resolved.end_date == "2026-01-15"
    assert resolved.extended_end_date == "2028-01-15"
    assert resolved.indicator_limit == 4000


def test_latest_end_date_wins_among_records_of_one_kind() -> None:
    records = [
        warranty("SN-1", formula="6M"),
        warranty("SN-1", code="STANDART_DEALER", formula="1Y6M"),
    ]

    assert resolve_warranty(records).end_date == "2026-07-15"


def test_null_bc_date_is_ignored() -> None:
    records = [
        warranty("SN-1", start="0001-01-01", formula="1Y", run=500),
        warranty("SN-1", code="EXTENDED_X", start="0001-01-01", formula="2Y"),
    ]

    assert resolve_warranty(records) == ResolvedWarranty(indicator_limit=500)


def test_unparseable_starting_date_is_ignored() -> None:
    assert resolve_warranty([warranty("SN-1", start="15/01/2025")]).start_date is None


def test_nowarranty_records_are_ignored_entirely() -> None:
    records = [
        warranty("SN-1", code="NOWARRANTY", start="2020-01-01", formula="9Y", run=99999),
        warranty("SN-1", start="2025-01-15", formula="1Y", run=100),
    ]

    resolved = resolve_warranty(records)

    assert resolved.start_date == "2025-01-15"
    assert resolved.end_date == "2026-01-15"
    assert resolved.indicator_limit == 100


@pytest.mark.parametrize("run", [0, 0.4, -5])
def test_non_positive_indicator_limit_is_none(run: float) -> None:
    assert resolve_warranty([warranty("SN-1", run=run)]).indicator_limit is None


def test_invalid_formula_keeps_start_date_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolved = resolve_warranty([warranty("SN-9", formula="CM+1D")])

    assert resolved.start_date == "2025-01-15"
    assert resolved.end_date is None
    record = next(r for r in caplog.records if r.levelname == "WARNING")
    assert getattr(record, "entity_key", None) == "SN-9"


def test_missing_formula_yields_no_end_date() -> None:
    assert resolve_warranty([warranty("SN-1", formula=None)]).end_date is None
