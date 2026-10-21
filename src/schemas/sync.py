"""Internal DTOs for sync state, audit records and dashboard snapshots."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from src.schemas.bc_vehicle import BCVehicleWarranty
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from src.schemas.schedule import ScheduleSettings
from src.schemas.warranty import ResolvedWarranty

AuditAction = Literal[
    "UPSERT",
    "ARCHIVE",
    "SKIPPED",
    "FAILED",
    "DRY_RUN_UPSERT",
    "DRY_RUN_ARCHIVE",
    "SINGLE_UPSERT",
    "SINGLE_ARCHIVE",
    "DRY_RUN_SINGLE_UPSERT",
    "DRY_RUN_SINGLE_ARCHIVE",
    "ABORTED",
    "TASK_RUN",
    "DRY_RUN_TASK",
]
SingleSyncOutcome = Literal["SYNCED", "DRY_RUN", "NOT_FOUND", "FAILED", "BUSY"]
ReleaseStatus = Literal["IDLE", "FAILED"]
ConfigFlag = Literal["dry_run", "auto_sync_enabled"]
RUNNING = "RUNNING"


class RuntimeConfig(ScheduleSettings):
    """Dashboard-controlled run flags and schedule (`sync_config`); override `.env` defaults."""

    dry_run: bool = True
    auto_sync_enabled: bool = False


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
    dry_run: bool = True
    full: bool = False


class SingleSyncDebug(BaseModel):
    """What a single-vehicle sync fetched from BC, resolved, and sent (or would send)."""

    warranties: list[BCVehicleWarranty]
    warranty: ResolvedWarranty
    payload: FrontuWorkspaceUpsert

    @property
    def wire_json(self) -> str:
        """The payload exactly as the Frontu client sends it (None fields omitted)."""
        return self.payload.model_dump_json(exclude_none=True, indent=2)


class SingleSyncResult(BaseModel):
    """Outcome of one targeted single-vehicle sync."""

    serial_no: str
    outcome: SingleSyncOutcome
    dry_run: bool
    action: AuditAction | None = None
    status_code: int | None = None
    error: str | None = None
    debug: SingleSyncDebug | None = None
