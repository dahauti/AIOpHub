"""Rows of the Module 2 SQLite registries (SPEC_TASKS section 4)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

OriginSystem = Literal["BC", "FRONTU"]
TaskSyncStatus = Literal["SYNCED", "PENDING", "FAILED", "VALIDATION_ERROR"]
LineEntityType = Literal["MATERIAL", "WORK", "COMMENT"]
LineSyncStatus = Literal["SYNCED", "FAILED"]

# Key prefix for a Frontu-born task rejected before BC assigned a Service Order No.
UNLINKED_ORDER_PREFIX = "UNLINKED:"


def unlinked_order_key(task_guid: str) -> str:
    return f"{UNLINKED_ORDER_PREFIX}{task_guid}"


def is_unlinked_order_key(order_no: str) -> bool:
    return order_no.startswith(UNLINKED_ORDER_PREFIX)


class TaskSyncState(BaseModel):
    """`task_sync_state` row: one BC Service Order linked to one Frontu task."""

    model_config = ConfigDict(frozen=True)

    service_order_no: str
    frontu_task_guid: str
    frontu_task_code: str | None = None
    header_hash: str
    origin_system: OriginSystem
    sync_status: TaskSyncStatus
    error_message: str | None = None


class TaskLineSyncState(BaseModel):
    """`task_line_sync_state` row: one BC line linked to one Frontu material/work."""

    model_config = ConfigDict(frozen=True)

    service_order_no: str
    line_no: int
    frontu_entity_guid: str
    entity_type: LineEntityType
    line_hash: str
    sync_status: LineSyncStatus
