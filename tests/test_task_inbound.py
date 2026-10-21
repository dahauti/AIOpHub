"""TaskInboundService: Frontu delta -> BC status, line ingest, watermark."""

from decimal import Decimal

import pytest

from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.domain.frontu_models import FrontuMaterial, FrontuTask, FrontuWork
from src.domain.task_state import TaskSyncState
from src.services.task_inbound import STREAM, TaskInboundService
from tests.task_fakes import NOW, FakeBC, FakeFrontu, Stores, stores

__all__ = ["stores"]


def _task(guid: str, status: int, version: int, system_id: str | None = "SO00001") -> FrontuTask:
    return FrontuTask(
        guid=guid,
        code="T-7",
        systemId=system_id,
        status=status,
        version=version,
        title="Repair",
        client="client-guid",
    )


def _service(bc: FakeBC, frontu: FakeFrontu, stores: Stores) -> TaskInboundService:
    return TaskInboundService(
        bc, frontu, stores.watermarks, stores.states, initial_version=0, now=lambda: NOW
    )


def _bc_order(status: str) -> BCServiceOrderHeader:
    return BCServiceOrderHeader(No="SO00001", DocumentStatus=status)


@pytest.mark.asyncio
async def test_linked_task_ingests_lines_and_maps_status(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.orders["SO00001"] = _bc_order("TASKERYJE")
    bc.lines["SO00001"] = [
        BCServiceOrderLine(DocumentNo="SO00001", LineNo=10000, Frontu_Integration_Line_GUID="m-old")
    ]
    frontu.delta = [_task("t-1", 400, 55)]
    frontu.materials["t-1"] = [
        FrontuMaterial(guid="m-old", systemId="ITEM-1", task="t-1", amount=1),
        FrontuMaterial(guid="m-new", systemId="ITEM-2", task="t-1", amount=2.5),
        FrontuMaterial(guid="m-free", systemId="", task="t-1", amount=3),
        FrontuMaterial(guid=None, systemId="ITEM-3", task="t-1", amount=1),
    ]
    frontu.works["t-1"] = [FrontuWork(guid="w-1", systemId="RES-1", task="t-1", duration=45)]

    result = await _service(bc, frontu, stores).run()

    assert result.synced == 1
    assert frontu.versions == [0]
    created = [(ln.LineNo, ln.Type, ln.No, ln.Quantity) for ln in bc.created_lines]
    assert created == [
        (20000, "Item", "ITEM-2", Decimal("2.5")),
        (30000, " ", None, Decimal(0)),
        (40000, "Resource", "RES-1", Decimal("0.75")),
    ]
    comment = bc.created_lines[1]
    assert comment.Description == "Unmapped Frontu material: (untitled) (3)"
    assert comment.Frontu_Integration_Line_GUID == "m-free"
    assert bc.header_patches[0][1]["DocumentStatus"] == "DONE"
    types = [s.entity_type for s in await stores.states.get_lines("SO00001")]
    assert types == ["MATERIAL", "COMMENT", "WORK"]
    assert await stores.watermarks.get_version_watermark(STREAM) == 55


@pytest.mark.asyncio
async def test_line_updates_blocked_by_current_bc_status(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.orders["SO00001"] = _bc_order("IN PROGRESS")
    frontu.delta = [_task("t-1", 400, 3)]
    frontu.materials["t-1"] = [FrontuMaterial(guid="m-1", systemId="I", task="t-1", amount=1)]

    await _service(bc, frontu, stores).run()

    assert bc.created_lines == []
    assert bc.header_patches[0][1]["DocumentStatus"] == "DONE"


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen", ["APPROVED", "ABORTED"])
async def test_frozen_bc_order_is_skipped(stores: Stores, frozen: str) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.orders["SO00001"] = _bc_order(frozen)
    frontu.delta = [_task("t-1", 110, 3)]

    result = await _service(bc, frontu, stores).run()

    assert result.skipped == 1
    assert bc.header_patches == [] and bc.created_lines == []


@pytest.mark.asyncio
async def test_missing_bc_order_and_missing_client_are_validation_errors(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    orphan = _task("t-2", 101, 4, system_id=None).model_copy(update={"client": None})
    frontu.delta = [_task("t-1", 101, 3, system_id="SO-GONE"), orphan]

    result = await _service(bc, frontu, stores).run()

    assert result.validation_errors == 2
    assert await stores.watermarks.get_version_watermark(STREAM) == 4


@pytest.mark.asyncio
async def test_failure_marks_state_and_stops_watermark_below_failed_version(
    stores: Stores,
) -> None:
    await stores.watermarks.set_version_watermark(STREAM, 10)
    await stores.states.save_task(
        TaskSyncState(
            service_order_no="SO00001",
            frontu_task_guid="t-1",
            header_hash="h",
            origin_system="BC",
            sync_status="SYNCED",
        )
    )
    bc, frontu = FakeBC(), FakeFrontu()
    bc.orders["SO00001"] = _bc_order("TASKERYJE")
    bc.fail_patch_for = {"SO00001"}
    frontu.delta = [_task("t-1", 200, 15), _task("t-9", 200, 20, system_id="SO-GONE")]

    result = await _service(bc, frontu, stores).run()

    assert (result.failed, result.validation_errors) == (1, 1)
    assert frontu.versions == [10]
    state = await stores.states.get_by_task_guid("t-1")
    assert state is not None and state.sync_status == "FAILED"
    assert await stores.watermarks.get_version_watermark(STREAM) == 14
