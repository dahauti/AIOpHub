"""Operations Center DTOs: job runs and per-record outcomes (ADR 0003).

Every stored value is a language-neutral English ASCII code; LT/EN text comes only from
`src/services/error_catalog.py`.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, field_validator

ProcessId = Literal["workspaces_vehicles", "tasks_sync"]
TriggerKind = Literal["schedule", "manual", "canary"]
RunMode = Literal["DRY_RUN", "LIVE"]
RunStatus = Literal["running", "succeeded", "failed", "aborted", "skipped"]
OutcomeResult = Literal["ok", "skipped", "error"]
ErrorType = Literal["data", "system"]


def mode_of(dry_run: bool) -> RunMode:
    return "DRY_RUN" if dry_run else "LIVE"


class _UtcModel(BaseModel):
    """SQLite returns naive timestamps; every stored timestamp is UTC."""

    @field_validator("*", mode="after")
    @classmethod
    def _utc(cls, value: object) -> object:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class JobRunStart(_UtcModel):
    run_id: str
    process_id: ProcessId
    trigger_kind: TriggerKind
    mode: RunMode
    profile: str
    started_at: datetime
    trace_id: str


class JobRunFinish(_UtcModel):
    run_id: str
    status: RunStatus
    mode: RunMode
    finished_at: datetime
    count_ok: int = 0
    count_skipped: int = 0
    count_failed: int = 0
    error_code: str | None = None
    error_summary: str | None = None


class JobRun(JobRunStart):
    status: RunStatus
    finished_at: datetime | None = None
    count_ok: int = 0
    count_skipped: int = 0
    count_failed: int = 0
    error_code: str | None = None
    error_summary: str | None = None


class RecordOutcome(_UtcModel):
    outcome_id: str
    run_id: str
    object_type: str
    object_id: str
    result: OutcomeResult
    error_code: str | None = None
    error_type: ErrorType | None = None
    params: dict[str, str] = {}
    technical_detail: str | None = None
    http_status: int | None = None
    trace_id: str
    recorded_at: datetime
