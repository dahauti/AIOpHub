"""Frontu references of a BC order; a reference BC names but Frontu lacks is a data error."""

from pydantic import BaseModel, ConfigDict

from src.domain.bc_models import BCServiceOrderHeader
from src.domain.task_builders import FrontuRefs
from src.services.ports import FrontuTasksPort


class RefMiss(BaseModel):
    """Error-catalog code plus the `systemId` Frontu did not know."""

    model_config = ConfigDict(frozen=True)

    code: str
    params: dict[str, str]


async def resolve_refs(
    frontu: FrontuTasksPort, order: BCServiceOrderHeader
) -> FrontuRefs | RefMiss:
    """Client, then workspace and task type when BC names them; first miss wins (reads only)."""
    customer = order.SelltoCustomerNo or ""
    client = await frontu.find_client_guid(customer) if customer else None
    if client is None:
        return RefMiss(code="task.no_client_match", params={"customer_no": customer})
    # No workspace match is not a stop: BC allows orders without a vehicle (ADR 0004).
    workspace = (
        await frontu.find_workspace_guid(order.VehicleSerialNo) if order.VehicleSerialNo else None
    )
    task_type: str | None = None
    if order.DealType:
        task_type = await frontu.find_task_type_guid(order.DealType)
        if task_type is None:
            return RefMiss(code="task.task_type_not_found", params={"task_type": order.DealType})
    return FrontuRefs(client=client, workspace=workspace, task_type=task_type)
