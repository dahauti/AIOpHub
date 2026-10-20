"""Internal DTOs for sync state, audit records and dashboard snapshots."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

AuditAction = Literal["UPSERT", "ARCHIVE", "SKIPPED", "FAILED", "DRY_RUN_UPSERT", "DRY_RUN_ARCHIVE"]
ReleaseStatus = Literal["IDLE", "FAILED"]
RUNNING = "RUNNING"


class AuditEntry(BaseModel):
    """One sync transaction to append to `sync_audit_log`."""

    model_config = ConfigDict(frozen=True)

    entity_key: str
    action: AuditAction
    status_code: int | None = None
    payload: dict[str, JsonValue] | None = None
    error_message: str | None = None


class AuditRecord(BaseModel):
    """A persisted audit row, as read back for display."""

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    entity_key: str
    action: str
    status_code: int | None = None
    error_message: str | None = None


class DashboardSnapshot(BaseModel):
    """Module state, today's counters and recent audit rows."""

    status: str = "IDLE"
    cursor_timestamp: datetime | None = None
    updated_at: datetime | None = None
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    planned: int = 0
    recent: list[AuditRecord] = []


class SyncRunResult(BaseModel):
    """Outcome counters of one completed pipeline run."""

    processed: int = 0
    skipped: int = 0
    failed: int = 0
    planned: int = 0
    cursor: datetime
