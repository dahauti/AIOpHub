"""BC VehicleWarrList -> resolved warranty dates and limits (SPEC section 9.2-9.3)."""

import calendar
import logging
import re
from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from src.schemas.bc_vehicle import BCVehicleWarranty

logger = logging.getLogger(__name__)

NO_WARRANTY = "NOWARRANTY"
EXTENDED_PREFIX = "EXTENDED_"
BC_NULL_DATE = "0001-01-01"
# A whole formula: one or more `<n><unit>` segments, optionally joined by `+` (e.g. `1Y+6M`).
_FORMULA = re.compile(r"\+?\d+[YMD](?:\+?\d+[YMD])*")
_SEGMENT = re.compile(r"(\d+)([YMD])")


class ResolvedWarranty(BaseModel):
    """Warranty facts for one vehicle, dates formatted as `YYYY-MM-DD`."""

    model_config = ConfigDict(frozen=True)

    start_date: str | None = None
    end_date: str | None = None
    extended_end_date: str | None = None
    indicator_limit: int | None = None


def _add_months(day: date, months: int) -> date:
    """Shift by whole months, clamping to the target month's last day."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def apply_date_formula(start: date, formula: str | None) -> date | None:
    """Apply a BC date formula such as `1Y`, `1Y6M` or `2Y+6M`; None if unsupported.

    Only forward Year/Month/Day segments are supported; anything else (e.g. `CM`, `-1D`)
    is rejected as a whole rather than partially applied.
    """
    if formula is None:
        return None
    normalized = formula.upper().replace(" ", "").strip("<>")
    if not _FORMULA.fullmatch(normalized):
        return None
    result = start
    try:
        for amount, unit in _SEGMENT.findall(normalized):
            if unit == "D":
                result += timedelta(days=int(amount))
            else:
                result = _add_months(result, int(amount) * (12 if unit == "Y" else 1))
    except (ValueError, OverflowError):
        return None
    return result


def _starting_date(record: BCVehicleWarranty) -> date | None:
    if record.StartingDate == BC_NULL_DATE:
        return None
    try:
        return date.fromisoformat(record.StartingDate)
    except ValueError:
        return None


def _end_date(record: BCVehicleWarranty) -> date | None:
    start = _starting_date(record)
    if start is None or record.TermDateFormula is None:
        return None
    end = apply_date_formula(start, record.TermDateFormula)
    if end is None:
        logger.warning(
            "Unsupported BC warranty date formula %r",
            record.TermDateFormula,
            extra={"entity_key": record.VehicleSerialNo, "warranty": record.WarrantyTypeCode},
        )
    return end


def _latest_end(records: Iterable[BCVehicleWarranty]) -> str | None:
    ends = [end for end in map(_end_date, records) if end is not None]
    return max(ends).isoformat() if ends else None


def resolve_warranty(records: Sequence[BCVehicleWarranty]) -> ResolvedWarranty:
    """Collapse a vehicle's active warranty rows; `NOWARRANTY` rows are ignored."""
    covered = [r for r in records if r.WarrantyTypeCode.upper() != NO_WARRANTY]
    extended = [r for r in covered if r.WarrantyTypeCode.upper().startswith(EXTENDED_PREFIX)]
    standard = [r for r in covered if not r.WarrantyTypeCode.upper().startswith(EXTENDED_PREFIX)]
    starts = [start for start in map(_starting_date, covered) if start is not None]
    limit = int(max((r.VariableFieldRun1 for r in covered), default=0))
    return ResolvedWarranty(
        start_date=min(starts).isoformat() if starts else None,
        end_date=_latest_end(standard),
        extended_end_date=_latest_end(extended),
        indicator_limit=limit if limit > 0 else None,
    )
