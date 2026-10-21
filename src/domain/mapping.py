"""BC Document Status mapping, line filters, comment-line fallback and SHA-256 fingerprints."""

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.domain.bc_models import COMMENT_LINE_TYPE, BCServiceOrderHeader, BCServiceOrderLine

# BC TG_Lines field lengths.
DESCRIPTION_MAX = 100
DESCRIPTION_2_MAX = 50

UnmappedSource = Literal["MATERIAL", "WORK"]


class DocumentStatusRule(BaseModel):
    """One row of BC `Document Status List` keyed by Frontu numeric status (SPEC_TASKS 3.1)."""

    model_config = ConfigDict(frozen=True)

    bc_status: str
    no_frontu_updates: bool = False
    no_frontu_line_updates: bool = False


FALLBACK_STATUS = DocumentStatusRule(bc_status="TASKERYJE")

STATUS_RULES: dict[int, DocumentStatusRule] = {
    100: DocumentStatusRule(bc_status="INACTIVE", no_frontu_line_updates=True),
    101: DocumentStatusRule(bc_status="SYNCED"),
    102: DocumentStatusRule(bc_status="UNASSGINED"),
    110: DocumentStatusRule(bc_status="REOPEN"),
    200: DocumentStatusRule(bc_status="IN PROGRESS", no_frontu_line_updates=True),
    300: DocumentStatusRule(bc_status="TASKER STOPPED", no_frontu_line_updates=True),
    400: DocumentStatusRule(bc_status="DONE", no_frontu_line_updates=True),
    401: DocumentStatusRule(bc_status="DONE WITH NOTE", no_frontu_line_updates=True),
    402: DocumentStatusRule(bc_status="APPROVED", no_frontu_updates=True),
    500: DocumentStatusRule(bc_status="TASKER UNSIGN", no_frontu_line_updates=True),
    600: DocumentStatusRule(bc_status="ABORTED", no_frontu_updates=True),
}

# Header fields that change on every sync round-trip and must not affect the fingerprint.
_VOLATILE_HEADER_FIELDS = {"SystemModifiedAt", "Last_Tasker_Sinhr_Time"}


def status_rule(status_code: int | None) -> DocumentStatusRule:
    """Rule for a Frontu status code; unknown or missing codes use the `TASKERYJE` fallback."""
    if status_code is None:
        return FALLBACK_STATUS
    return STATUS_RULES.get(status_code, FALLBACK_STATUS)


def frontu_code_for_bc_status(bc_status: str | None) -> int | None:
    """Frontu code whose rule targets this BC `DocumentStatus`; None for unlisted statuses."""
    for code, rule in STATUS_RULES.items():
        if rule.bc_status == bc_status:
            return code
    return None


def map_frontu_status(status_code: int | None) -> str:
    """Target BC `DocumentStatus` for a Frontu numeric status code."""
    return status_rule(status_code).bc_status


def is_frozen(status_code: int | None) -> bool:
    """True when `No Frontu Updates` is set: the BC order accepts no Frontu changes at all."""
    return status_rule(status_code).no_frontu_updates


def is_line_update_allowed(status_code: int) -> bool:
    """False when the status sets `No Frontu Line Updates` or freezes the order entirely."""
    rule = status_rule(status_code)
    return not (rule.no_frontu_line_updates or rule.no_frontu_updates)


def is_unlinked(line: BCServiceOrderLine) -> bool:
    """True when the line has no Frontu entity GUID yet (SPEC_TASKS 3.2)."""
    guid = line.Frontu_Integration_Line_GUID
    return not guid or guid.strip() == ""


def filter_unlinked_bc_lines(lines: list[BCServiceOrderLine]) -> list[BCServiceOrderLine]:
    """Lines still to push to Frontu; lines carrying a GUID are immutable and skipped."""
    return [line for line in lines if is_unlinked(line)]


def build_unmapped_comment_line(
    document_no: str,
    line_no: int,
    source: UnmappedSource,
    title: str | None,
    quantity: Decimal,
    unit: str | None,
    frontu_code: str | None = None,
) -> BCServiceOrderLine:
    """Comment line (`Type = ' '`) recording a Frontu material/work with no BC catalog match."""
    label = "material" if source == "MATERIAL" else "work"
    amount = f"{quantity.normalize():f} {unit or ''}".strip()
    note = f"Unmapped Frontu {label}: {title or '(untitled)'} ({amount})"
    detail = f"Frontu code: {frontu_code}" if frontu_code else None
    return BCServiceOrderLine(
        DocumentNo=document_no,
        LineNo=line_no,
        Type=COMMENT_LINE_TYPE,
        Description=note[:DESCRIPTION_MAX],
        Description_2=detail[:DESCRIPTION_2_MAX] if detail else None,
    )


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def header_hash(header: BCServiceOrderHeader) -> str:
    """SHA-256 of the header JSON, excluding volatile sync timestamps."""
    return _sha256(header.model_dump_json(exclude=_VOLATILE_HEADER_FIELDS))


def line_hash(line: BCServiceOrderLine) -> str:
    """SHA-256 of the line JSON, excluding the Frontu GUID written back after the push."""
    return _sha256(line.model_dump_json(exclude={"Frontu_Integration_Line_GUID"}))
