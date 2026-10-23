"""Pure builders between BC Service Orders/lines and Frontu tasks/materials/works."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, JsonValue

from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.domain.frontu_models import (
    FrontuMaterial,
    FrontuTask,
    FrontuTaskPayload,
    FrontuWork,
    hours_to_duration_minutes,
)
from src.domain.mapping import build_unmapped_comment_line, map_frontu_status

ITEM_LINE_TYPE = "Item"
RESOURCE_LINE_TYPE = "Resource"
LINE_NO_STEP = 10000
_MINUTES_PER_HOUR = Decimal(60)
# BC quantities carry 5 decimal places.
_BC_QUANTITY_STEP = Decimal("0.00001")


class FrontuRefs(BaseModel):
    """Resolved Frontu catalog GUIDs for one Service Order."""

    model_config = ConfigDict(frozen=True)

    client: str
    workspace: str | None = None
    task_type: str | None = None


def planned_time(header: BCServiceOrderHeader) -> str | None:
    """`PlannedServiceDate` as ISO date, joined with `OrderTime` when present."""
    if header.PlannedServiceDate is None:
        return None
    day = header.PlannedServiceDate.isoformat()
    return f"{day}T{header.OrderTime}" if header.OrderTime else day


def build_task_payload(
    header: BCServiceOrderHeader, order_no: str, refs: FrontuRefs, task_guid: str | None
) -> FrontuTaskPayload:
    """Frontu task upsert for a BC order; `task_guid` set updates the linked task."""
    return FrontuTaskPayload(
        guid=task_guid,
        systemId=order_no,
        title=header.Description or order_no,
        description=header.WorkDescription,
        plannedTime=planned_time(header),
        client=refs.client,
        workspace=refs.workspace,
        taskType=refs.task_type,
    )


def build_material(line: BCServiceOrderLine, task_guid: str) -> FrontuMaterial:
    return FrontuMaterial(systemId=line.No or "", task=task_guid, amount=float(line.Quantity))


def build_work(line: BCServiceOrderLine, task_guid: str) -> FrontuWork:
    duration = hours_to_duration_minutes(line.Quantity)
    return FrontuWork(systemId=line.No or "", task=task_guid, duration=duration)


# Keys of `tasker_patch`, in order; the /centras LIVE dialog lists them (test-guarded).
TASKER_PATCH_FIELDS = (
    "DocumentStatus",
    "Tasker_ID",
    "Tasker_Document_No",
    "Last_Tasker_Sinhr_Time",
)


def tasker_patch(task: FrontuTask, status: str, synced_at: datetime) -> dict[str, JsonValue]:
    """BC header fields linking an order to its Frontu task."""
    return {
        "DocumentStatus": status,
        "Tasker_ID": task.guid,
        "Tasker_Document_No": task.code,
        "Last_Tasker_Sinhr_Time": synced_at.isoformat(),
    }


def build_order_from_task(task: FrontuTask, customer_no: str) -> BCServiceOrderHeader:
    """BC order for a Frontu-born task, initialised with the mapped status (never FRONTU)."""
    return BCServiceOrderHeader(
        SelltoCustomerNo=customer_no,
        DocumentStatus=map_frontu_status(task.status),
        Tasker_ID=task.guid,
        Tasker_Document_No=task.code,
        Description=task.title,
        WorkDescription=task.description,
    )


def build_writeback_payload(task: FrontuTask, order_no: str, client: str) -> FrontuTaskPayload:
    """Task upsert writing the BC Service Order No back to Frontu `systemId`."""
    return FrontuTaskPayload(
        guid=task.guid,
        systemId=order_no,
        title=task.title or order_no,
        description=task.description,
        plannedTime=task.plannedTime,
        client=client,
        workspace=task.workspace,
        taskType=task.taskType,
        user=task.user,
    )


def line_from_material(order_no: str, line_no: int, material: FrontuMaterial) -> BCServiceOrderLine:
    """Item line for a mapped material, otherwise a comment line (95/5 fallback)."""
    if not material.systemId.strip():
        amount = Decimal(str(material.amount))
        comment = build_unmapped_comment_line(
            order_no, line_no, "MATERIAL", None, amount, None, material.guid
        )
        return comment.model_copy(update={"Frontu_Integration_Line_GUID": material.guid})
    return BCServiceOrderLine(
        DocumentNo=order_no,
        LineNo=line_no,
        Type=ITEM_LINE_TYPE,
        No=material.systemId,
        Quantity=Decimal(str(material.amount)),
        Frontu_Integration_Line_GUID=material.guid,
    )


def line_from_work(order_no: str, line_no: int, work: FrontuWork) -> BCServiceOrderLine:
    """Resource line (hours) for a mapped work, otherwise a comment line (95/5 fallback)."""
    hours = (Decimal(work.duration) / _MINUTES_PER_HOUR).quantize(_BC_QUANTITY_STEP)
    if not work.systemId.strip():
        comment = build_unmapped_comment_line(
            order_no, line_no, "WORK", None, hours, "h", work.guid
        )
        return comment.model_copy(update={"Frontu_Integration_Line_GUID": work.guid})
    return BCServiceOrderLine(
        DocumentNo=order_no,
        LineNo=line_no,
        Type=RESOURCE_LINE_TYPE,
        No=work.systemId,
        Quantity=hours,
        Frontu_Integration_Line_GUID=work.guid,
    )
