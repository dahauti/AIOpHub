"""Task cockpit read models: last-run totals, canary outcome and the card snapshot."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.config import AppEnv
from src.domain.canary import CanaryTarget
from src.services.task_runner import TaskRunReport

CanaryState = Literal["RUNNING", "DONE", "BUSY", "FAILED"]


class CanaryOutcome(BaseModel):
    """Latest UI canary in this process; BUSY when another run held the lock."""

    model_config = ConfigDict(frozen=True)

    target: CanaryTarget
    state: CanaryState
    summary: str | None = None

    @classmethod
    def from_report(cls, target: CanaryTarget, report: TaskRunReport) -> "CanaryOutcome":
        result = report.runs[0].result
        mode = "dry run" if report.dry_run else "live"
        summary = f"synced={result.synced} skipped={result.skipped} errors={result.errors} ({mode})"
        return cls(target=target, state="DONE", summary=summary)


class LastRun(BaseModel):
    """Totals of the latest TASK_RUN entry across its directions."""

    model_config = ConfigDict(frozen=True)

    finished_at: datetime
    dry_run: bool
    processed: int
    synced: int
    errors: int
    elapsed_seconds: float

    @classmethod
    def from_report(cls, finished_at: datetime, report: TaskRunReport) -> "LastRun":
        return cls(
            finished_at=finished_at,
            dry_run=report.dry_run,
            processed=sum(run.result.processed for run in report.runs),
            synced=sum(run.result.synced for run in report.runs),
            errors=sum(run.result.errors for run in report.runs),
            elapsed_seconds=sum(run.elapsed_seconds for run in report.runs),
        )


class TaskCockpit(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    updated_at: datetime | None
    running: bool
    dry_run: bool
    app_env: AppEnv
    last_run: LastRun | None
    bc_watermark: datetime | None = None
    frontu_version: int | None = None
    canary: CanaryOutcome | None = None
    frontu_base_url: str | None = None
    bc_company: str | None = None
    bc_profile: str | None = None
