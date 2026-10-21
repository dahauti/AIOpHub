"""Step-by-step canary telemetry: source fetch, mapping evaluation and target mutation.

Steps are derived after the run from its counters and the `DRY_RUN_TASK` rows it wrote, so
field-level mapping detail exists for dry runs; a live run reports its counters only.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.adapters.resilience import ExternalServiceError
from src.domain.canary import CanaryTarget
from src.repositories.event_repository import AuditEvent
from src.services.task_runner import TaskRunReport

StepState = Literal["ok", "simulated", "skipped", "failed"]
HEADER_OPERATIONS = ("frontu.upsert_task", "bc.update_order_header", "bc.create_service_order")
SYSTEMS = {"order": ("BC OData", "Frontu v2"), "task": ("Frontu v2", "BC OData")}


class CanaryStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    state: StepState
    detail: str


class HttpFailure(BaseModel):
    """The exact failing call: status (None = no HTTP response), URL and vendor error body."""

    model_config = ConfigDict(frozen=True)

    service: str
    status_code: int | None
    url: str | None
    body: str


def failure_of(error: Exception) -> HttpFailure:
    if isinstance(error, ExternalServiceError):
        return HttpFailure(
            service=error.service, status_code=error.status_code, url=error.url, body=error.detail
        )
    return HttpFailure(service="engine", status_code=None, url=None, body=repr(error))


def _operation(event: AuditEvent) -> str:
    operation = (event.payload or {}).get("operation")
    return operation if isinstance(operation, str) else event.action


def _mapped_fields(writes: list[AuditEvent]) -> list[str]:
    for event in writes:
        body = (event.payload or {}).get("body")
        if _operation(event) in HEADER_OPERATIONS and isinstance(body, dict):
            return sorted(body)
    return []


def success_steps(
    target: CanaryTarget, report: TaskRunReport, writes: list[AuditEvent]
) -> tuple[CanaryStep, ...]:
    source, sink = SYSTEMS[target.kind]
    result = report.runs[0].result
    if result.processed == result.validation_errors:
        invalid = result.validation_errors
        reason = f"{target.value} not found or not eligible (validation_errors={invalid})"
        return (
            CanaryStep(number=1, title=f"{source} fetch", state="failed", detail=reason),
            CanaryStep(number=2, title="Mapping evaluation", state="skipped", detail="No record"),
            CanaryStep(number=3, title=f"{sink} mutation", state="skipped", detail="No record"),
        )
    fetched = f"Fetched {target.value}: {result.processed} record(s) evaluated"
    fields = _mapped_fields(writes)
    lines = sum(1 for event in writes if _operation(event) not in HEADER_OPERATIONS)
    mapped = f"Mapped fields: {', '.join(fields)}" if fields else "Field detail: dry run only"
    mapping = f"{mapped}; {lines} line(s); fallback/unmapped: skipped={result.skipped}"
    mapping += f" failed={result.failed}"
    if report.dry_run:
        operations = ", ".join(_operation(event) for event in writes) or "none"
        sink_state: StepState = "simulated"
        mutation = f"DRY RUN: {len(writes)} write(s) simulated ({operations})"
    else:
        sink_state = "failed" if result.failed else "ok" if result.synced else "skipped"
        mutation = f"LIVE: synced={result.synced} failed={result.failed}"
    return (
        CanaryStep(number=1, title=f"{source} fetch", state="ok", detail=fetched),
        CanaryStep(number=2, title="Mapping evaluation", state="ok", detail=mapping),
        CanaryStep(number=3, title=f"{sink} mutation", state=sink_state, detail=mutation),
    )
