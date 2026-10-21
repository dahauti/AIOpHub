"""Audit log filter DTO: action group, SerialNo search, date range and a hard row cap."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.sync import AuditAction

AuditActionFilter = Literal["PROCESSED", "UPSERT", "ARCHIVE", "SKIPPED", "FAILED", "ALL_ERRORS"]
DateRange = Literal["today", "7d", "all"]

# Batch and single-vehicle writes share a filter; PROCESSED is exactly the "Processed today"
# KPI (confirmed upserts and archives); ALL_ERRORS also covers aborted runs.
ACTION_GROUPS: dict[AuditActionFilter, tuple[AuditAction, ...]] = {
    "PROCESSED": ("UPSERT", "ARCHIVE", "SINGLE_UPSERT", "SINGLE_ARCHIVE"),
    "UPSERT": ("UPSERT", "SINGLE_UPSERT"),
    "ARCHIVE": ("ARCHIVE", "SINGLE_ARCHIVE"),
    "SKIPPED": ("SKIPPED",),
    "FAILED": ("FAILED",),
    "ALL_ERRORS": ("FAILED", "ABORTED"),
}
ERROR_FILTERS: frozenset[AuditActionFilter] = frozenset({"FAILED", "ALL_ERRORS"})


class AuditQuery(BaseModel):
    """Validated audit filter; blank form values mean "no filter"."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    action: AuditActionFilter | None = None
    search: str | None = Field(default=None, max_length=64)
    date_range: DateRange = "today"
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("action", "search", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @property
    def actions(self) -> tuple[AuditAction, ...] | None:
        return ACTION_GROUPS[self.action] if self.action is not None else None

    @property
    def detailed(self) -> bool:
        """Error filters show full error messages and HTTP reasons."""
        return self.action in ERROR_FILTERS
