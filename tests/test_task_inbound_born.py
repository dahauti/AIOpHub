"""Frontu-born tasks: SelltoCustomerNo resolution, BC order creation and SO write-back."""

import pytest

from src.domain.frontu_models import FrontuTask, FrontuWork
from src.domain.task_state import TaskSyncState
from src.services.task_inbound import STREAM, TaskInboundService
from tests.task_fakes import NOW, FakeBC, FakeFrontu, Stores, stores

__all__ = ["stores"]


def _task(guid: str = "t-new", client: str | None = "client-guid", status: int = 200) -> FrontuTask:
    return FrontuTask(
        guid=guid,
        code="T-7",
        systemId=None,
        status=status,
        version=9,
        title="Repair",
        client=client,
    )


def _service(bc: FakeBC, frontu: FakeFrontu, stores: Stores) -> TaskInboundService:
    return TaskInboundService(
        bc, frontu, stores.watermarks, stores.states, initial_version=0, now=lambda: NOW
    )


@pytest.mark.asyncio
async def test_creates_bc_order_with_resolved_customer_and_writes_back(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    frontu.delta = [_task().model_copy(update={"systemId": " "})]
    frontu.works["t-new"] = [FrontuWork(guid="w-1", systemId="RES-1", task="t-new", duration=60)]

    result = await _service(bc, frontu, stores).run()

    assert result.synced == 1
    header = bc.created_orders[0]
    assert (header.No, header.SelltoCustomerNo, header.DocumentStatus, header.Tasker_ID) == (
        None,
        "C00010",
        "IN PROGRESS",
        "t-new",
    )
    assert bc.created_lines[0].DocumentNo == "SO90001"
    assert [(p.guid, p.systemId, p.client) for p in frontu.upserts] == [
        ("t-new", "SO90001", "client-guid")
    ]
    state = await stores.states.get_by_task_guid("t-new")
    assert state is not None
    assert (state.service_order_no, state.origin_system, state.sync_status) == (
        "SO90001",
        "FRONTU",
        "SYNCED",
    )


@pytest.mark.asyncio
async def test_retry_reuses_registered_order(stores: Stores) -> None:
    await stores.states.save_task(
        TaskSyncState(
            service_order_no="SO00042",
            frontu_task_guid="t-new",
            header_hash="h",
            origin_system="FRONTU",
            sync_status="PENDING",
        )
    )
    bc, frontu = FakeBC(), FakeFrontu()
    frontu.delta = [_task(status=101)]

    await _service(bc, frontu, stores).run()

    assert bc.created_orders == []
    assert frontu.upserts[0].systemId == "SO00042"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "message"),
    [(None, "has no client"), ("unknown-guid", "has no BC customer systemId")],
)
async def test_unresolved_customer_records_validation_error_without_bc_writes(
    stores: Stores, client: str | None, message: str
) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    frontu.delta = [_task(client=client)]

    result = await _service(bc, frontu, stores).run()

    assert result.validation_errors == 1
    assert bc.created_orders == [] and bc.created_lines == [] and frontu.upserts == []
    state = await stores.states.get_by_task_guid("t-new")
    assert state is not None
    assert (state.service_order_no, state.sync_status) == ("UNLINKED:t-new", "VALIDATION_ERROR")
    assert state.error_message is not None and message in state.error_message
    assert await stores.watermarks.get_version_watermark(STREAM) == 9


@pytest.mark.asyncio
async def test_resolved_customer_replaces_validation_placeholder(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    frontu.client_customers = {}
    frontu.delta = [_task()]
    service = _service(bc, frontu, stores)
    await service.run()

    frontu.client_customers = {"client-guid": "C00077"}
    result = await service.run()

    assert result.synced == 1
    assert bc.created_orders[0].SelltoCustomerNo == "C00077"
    assert await stores.states.get_by_order("UNLINKED:t-new") is None
    state = await stores.states.get_by_task_guid("t-new")
    assert state is not None
    assert (state.service_order_no, state.sync_status) == ("SO90001", "SYNCED")
