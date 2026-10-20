"""Shared audit helpers for the batch and single-vehicle pipelines."""

import logging
from typing import Literal

from src.pipelines.ports import RunState
from src.repositories.sync_repository import SyncRepository
from src.schemas.bc_vehicle import BCODataVehicle
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from src.schemas.sync import AuditAction, AuditEntry

logger = logging.getLogger(__name__)

WriteKind = Literal["UPSERT", "ARCHIVE"]
# (write kind, single-vehicle run, dry run) -> audit action
_ACTIONS: dict[tuple[WriteKind, bool, bool], AuditAction] = {
    ("UPSERT", False, False): "UPSERT",
    ("ARCHIVE", False, False): "ARCHIVE",
    ("UPSERT", False, True): "DRY_RUN_UPSERT",
    ("ARCHIVE", False, True): "DRY_RUN_ARCHIVE",
    ("UPSERT", True, False): "SINGLE_UPSERT",
    ("ARCHIVE", True, False): "SINGLE_ARCHIVE",
    ("UPSERT", True, True): "DRY_RUN_SINGLE_UPSERT",
    ("ARCHIVE", True, True): "DRY_RUN_SINGLE_ARCHIVE",
}


def write_action(vehicle: BCODataVehicle, *, single: bool, dry_run: bool) -> AuditAction:
    """Audit action for a planned or confirmed workspace write."""
    return _ACTIONS[("ARCHIVE" if vehicle.Blocked else "UPSERT", single, dry_run)]


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
) -> None:
    """Count and audit a failed vehicle; the cursor is held at the earliest failure."""
    modified = vehicle.SystemModifiedAt
    if run.earliest_failure is None or modified < run.earliest_failure:
        run.earliest_failure = modified
    run.failed += 1
    logger.warning(
        "Vehicle sync failed: %s",
        detail,
        extra={"run_id": run.run_id, "entity_key": vehicle.SerialNo, "status": status_code},
    )
    await record_audit(repo, vehicle.SerialNo, action, status_code, payload, detail)
