"""TaskOutboundService: BC FRONTU orders -> Frontu tasks, lines, BC patch, state, watermark."""

from datetime import timedelta
from decimal import Decimal

import pytest

from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.schemas.operations import OutcomeResult
from src.services.error_catalog import error_type
from src.services.task_outbound import STREAM, TaskOutboundService
from tests.task_fakes import NOW, FakeBC, FakeFrontu, Stores, stores

__all__ = ["stores"]

INITIAL = NOW - timedelta(days=1)


def _order(no: str, minutes: int = 0, **fields: str) -> BCServiceOrderHeader:
    return BCServiceOrderHeader.model_validate(
        {
            "No": no,
            "SelltoCustomerNo": "C00010",
            "VehicleSerialNo": "SN-1",
            "DealType": "SERVICE",
            "DocumentStatus": "FRONTU",
            "Description": "Brake check",
            "PlannedServiceDate": "2026-10-22",
            "OrderTime": "08:30:00",
            "SystemModifiedAt": (NOW + timedelta(minutes=minutes)).isoformat(),
            **fields,
        }
    )


def _line(
    no: int, line_type: str, item: str, qty: str, guid: str | None = None
) -> BCServiceOrderLine:
    return BCServiceOrderLine(
        DocumentNo="SO00001",
        LineNo=no,
        Type=line_type,
        No=item,
        Quantity=Decimal(qty),
        Frontu_Integration_Line_GUID=guid,
    )


def _service(bc: FakeBC, frontu: FakeFrontu, stores: Stores) -> TaskOutboundService:
    return TaskOutboundService(
        bc, frontu, stores.watermarks, stores.states, initial_watermark=INITIAL, now=lambda: NOW
    )


@pytest.mark.asyncio
async def test_dispatches_order_pushes_unlinked_lines_and_patches_bc(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.released = [_order("SO00001")]
    bc.lines["SO00001"] = [
        _line(10000, "Item", "ITEM-1", "2"),
        _line(20000, "Resource", "RES-1", "1.5"),
        _line(30000, "Item", "ITEM-2", "1", guid="already-linked"),
        _line(40000, " ", "", "0"),
    ]

    result = await _service(bc, frontu, stores).run()

    assert result.synced == 1
    assert bc.watermarks == [INITIAL.isoformat()]
    payload = frontu.upserts[0]
    assert (payload.systemId, payload.client, payload.workspace, payload.taskType) == (
        "SO00001",
        "client-guid",
        "ws-guid",
        "type-guid",
    )
    assert payload.plannedTime == "2026-10-22T08:30:00"
    assert [(m.systemId, m.amount, m.task) for m in frontu.pushed_materials] == [
        ("ITEM-1", 2.0, "task-SO00001")
    ]
    assert [(w.systemId, w.duration) for w in frontu.pushed_works] == [("RES-1", 90)]
    assert bc.line_patches == [
        ("SO00001", 10000, {"Frontu_Integration_Line_GUID": "mat-1"}),
        ("SO00001", 20000, {"Frontu_Integration_Line_GUID": "work-1"}),
    ]
    assert bc.header_patches == [
        (
            "SO00001",
            {
                "DocumentStatus": "TASKERYJE",
                "Tasker_ID": "task-SO00001",
                "Tasker_Document_No": "T-100",
                "Last_Tasker_Sinhr_Time": NOW.isoformat(),
            },
        )
    ]
    state = await stores.states.get_by_order("SO00001")
    assert state is not None
    assert (state.frontu_task_guid, state.origin_system, state.sync_status) == (
        "task-SO00001",
        "BC",
        "SYNCED",
    )
    assert len(await stores.states.get_lines("SO00001")) == 2
    assert await stores.watermarks.get_raw_watermark(STREAM) == NOW


@pytest.mark.asyncio
async def test_rerun_with_unchanged_header_skips_task_and_line_repush(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.released = [_order("SO00001")]
    bc.lines["SO00001"] = [_line(10000, "Item", "ITEM-1", "2")]
    service = _service(bc, frontu, stores)

    await service.run()
    await service.run()

    assert len(frontu.upserts) == 1
    assert len(frontu.pushed_materials) == 1
    assert len(bc.line_patches) == 2
    assert len(bc.header_patches) == 2


@pytest.mark.asyncio
async def test_unknown_client_is_validation_error_without_side_effects(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.released = [_order("SO00001", SelltoCustomerNo="UNKNOWN")]

    result = await _service(bc, frontu, stores).run()

    assert result.validation_errors == 1
    assert frontu.upserts == [] and bc.header_patches == []
    assert await stores.watermarks.get_raw_watermark(STREAM) == NOW


class _Codes:
    """OutcomeSink keeping (object_id, result, error_code, params) per recorded outcome."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, OutcomeResult, str | None, dict[str, str] | None]] = []

    async def record_outcome(
        self,
        object_type: str,
        object_id: str,
        result: OutcomeResult,
        *,
        error_code: str | None = None,
        params: dict[str, str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.rows.append((object_id, result, error_code, params))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "code", "params"),
    [
        ({"SelltoCustomerNo": "UNKNOWN"}, "task.no_client_match", {"customer_no": "UNKNOWN"}),
        ({"VehicleSerialNo": "SN-404"}, "task.workspace_not_found", {"serial_no": "SN-404"}),
        ({"DealType": "NOPE"}, "task.task_type_not_found", {"task_type": "NOPE"}),
    ],
)
async def test_missing_frontu_reference_is_data_error_naming_it_without_writes(
    stores: Stores, fields: dict[str, str], code: str, params: dict[str, str]
) -> None:
    bc, frontu, codes = FakeBC(), FakeFrontu(), _Codes()
    bc.released = [_order("SO00001", **fields)]
    service = TaskOutboundService(
        bc, frontu, stores.watermarks, stores.states, initial_watermark=INITIAL, outcomes=codes
    )

    result = await service.run()

    assert result.validation_errors == 1
    assert codes.rows == [("SO00001", "error", code, params)]
    assert error_type(code) == "data"
    assert frontu.upserts == [] and bc.header_patches == []
    assert await stores.states.get_by_order("SO00001") is None


@pytest.mark.asyncio
async def test_failed_order_records_state_and_holds_watermark(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.released = [_order("SO00001", 0), _order("SO00002", 5), _order("SO00003", 10)]
    bc.fail_patch_for = {"SO00002"}

    result = await _service(bc, frontu, stores).run()

    assert (result.synced, result.failed) == (2, 1)
    failed = await stores.states.get_by_order("SO00002")
    assert failed is not None and failed.sync_status == "FAILED"
    assert failed.error_message is not None and "gave up" in failed.error_message
    assert await stores.watermarks.get_raw_watermark(STREAM) == NOW + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_uses_buffered_watermark_and_never_moves_it_back(stores: Stores) -> None:
    await stores.watermarks.set_watermark(STREAM, NOW + timedelta(hours=1))
    bc, frontu = FakeBC(), FakeFrontu()
    bc.released = [_order("SO00001")]

    await _service(bc, frontu, stores).run()

    assert bc.watermarks == [(NOW + timedelta(minutes=45)).isoformat()]
    assert await stores.watermarks.get_raw_watermark(STREAM) == NOW + timedelta(hours=1)
