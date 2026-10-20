"""Shared audit and workspace-write helpers for the batch and single-vehicle pipelines."""

import logging
from typing import Literal

from src.pipelines.identity_resolver import IdentityDecision
from src.pipelines.ports import RunState, WorkspaceSink
from src.repositories.sync_repository import SyncRepository
from src.schemas.bc_vehicle import BCODataVehicle
from src.schemas.frontu_models import FrontuUpsertResult, FrontuWorkspaceUpsert
from src.schemas.sync import AuditAction, AuditEntry

logger = logging.getLogger(__name__)

WriteKind = Literal["UPSERT", "ADOPT", "ARCHIVE"]
# (write kind, single-vehicle run, dry run) -> audit action
_ACTIONS: dict[tuple[WriteKind, bool, bool], AuditAction] = {
    ("UPSERT", False, False): "UPSERT",
    ("ADOPT", False, False): "ADOPT",
    ("ARCHIVE", False, False): "ARCHIVE",
    ("UPSERT", False, True): "DRY_RUN_UPSERT",
    ("ADOPT", False, True): "DRY_RUN_ADOPT",
    ("ARCHIVE", False, True): "DRY_RUN_ARCHIVE",
    ("UPSERT", True, False): "SINGLE_UPSERT",
    ("ADOPT", True, False): "SINGLE_ADOPT",
    ("ARCHIVE", True, False): "SINGLE_ARCHIVE",
    ("UPSERT", True, True): "DRY_RUN_SINGLE_UPSERT",
    ("ADOPT", True, True): "DRY_RUN_SINGLE_ADOPT",
    ("ARCHIVE", True, True): "DRY_RUN_SINGLE_ARCHIVE",
}


def write_action(
    vehicle: BCODataVehicle, decision: IdentityDecision, *, single: bool, dry_run: bool
) -> AuditAction:
    """Audit action for a planned or confirmed workspace write."""
    kind: WriteKind = "ADOPT" if decision.kind == "ADOPT" else "UPSERT"
    return _ACTIONS[("ARCHIVE" if vehicle.Blocked else kind, single, dry_run)]


async def write_workspace(
    sink: WorkspaceSink, decision: IdentityDecision, payload: FrontuWorkspaceUpsert
) -> FrontuUpsertResult:
    """Adopt the resolved legacy object by id, otherwise use the native systemId upsert."""
    if decision.kind == "ADOPT" and decision.workspace_id is not None:
        return await sink.adopt_workspace(decision.workspace_id, payload)
    return await sink.upsert_workspace(payload)


async def record_audit(
    repo: SyncRepository,
    entity_key: str,
    action: AuditAction,
    status_code: int | None = None,
    payload: FrontuWorkspaceUpsert | None = None,
    error_message: str | None = None,
) -> None:
    body = payload.model_dump(mode="json") if payload is not None else None
    entry = AuditEntry(
        entity_key=entity_key,
        action=action,
        status_code=status_code,
        payload=body,
        error_message=error_message,
    )
    await repo.record_audit(entry)


async def record_failure(
    repo: SyncRepository,
    run: RunState,
    vehicle: BCODataVehicle,
    status_code: int | None,
    detail: str,
    payload: FrontuWorkspaceUpsert | None,
    action: AuditAction = "FAILED",
    *,
    hold_cursor: bool = True,
) -> None:
    """Count and audit a failed vehicle; retryable failures hold the cursor at the earliest."""
    modified = vehicle.SystemModifiedAt
    if hold_cursor and (run.earliest_failure is None or modified < run.earliest_failure):
        run.earliest_failure = modified
    run.failed += 1
    logger.warning(
        "Vehicle sync failed: %s",
        detail,
        extra={"run_id": run.run_id, "entity_key": vehicle.SerialNo, "status": status_code},
    )
    await record_audit(repo, vehicle.SerialNo, action, status_code, payload, detail)
