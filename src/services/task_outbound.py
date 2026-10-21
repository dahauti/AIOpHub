"""BC -> Frontu: dispatch `FRONTU` orders as tasks and push their unlinked lines."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from src.adapters.resilience import ExternalServiceError
from src.domain.bc_models import BCServiceOrderHeader
from src.domain.frontu_models import FrontuTask
from src.domain.mapping import FALLBACK_STATUS, header_hash
from src.domain.task_builders import FrontuRefs, build_task_payload, tasker_patch
from src.domain.task_state import TaskSyncState, TaskSyncStatus
from src.services.line_reconciler import LineReconciler
from src.services.ports import (
    MAX_ERROR_CHARS,
    BCTasksPort,
    FrontuTasksPort,
    Outcome,
    SyncRunResult,
    utc_now,
)
from src.storage.task_repository import TaskRepository
from src.storage.task_state_repository import TaskStateRepository

logger = logging.getLogger(__name__)

STREAM = "operative_tasks_bc"
# Only orders BC released for dispatch may be pushed, by the poll or by a canary.
DISPATCH_STATUS = "FRONTU"


class TaskOutboundService:
    """One polling pass over released BC orders; the watermark never passes a failed order."""

    def __init__(
        self,
        bc: BCTasksPort,
        frontu: FrontuTasksPort,
        watermarks: TaskRepository,
        states: TaskStateRepository,
        *,
        initial_watermark: datetime,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._bc = bc
        self._frontu = frontu
        self._watermarks = watermarks
        self._states = states
        self._lines = LineReconciler(bc, frontu, states)
        self._initial = initial_watermark
        self._now = now

    async def sync_single_order(self, order_no: str) -> SyncRunResult:
        """Canary: push one released BC order to Frontu; the stream watermark is untouched."""
        result = SyncRunResult()
        order = await self._bc.fetch_order(order_no)
        if order is None:
            logger.warning("Canary: BC order %s not found", order_no)
            result.record("VALIDATION_ERROR")
        elif order.DocumentStatus != DISPATCH_STATUS:
            logger.warning(
                "Canary: BC order %s has status %r, not %r; nothing pushed",
                order_no,
                order.DocumentStatus,
                DISPATCH_STATUS,
            )
            result.record("SKIPPED")
        else:
            result.record(await self._sync_order(order, uuid.uuid4().hex))
        return result

    async def run(self) -> SyncRunResult:
        run_id = uuid.uuid4().hex
        since = await self._watermarks.get_watermark(STREAM) or self._initial
        orders = await self._bc.fetch_released_orders(since.isoformat())
        result = SyncRunResult()
        failed_at: list[datetime] = []
        for order in orders:
            outcome = await self._sync_order(order, run_id)
            result.record(outcome)
            if outcome == "FAILED" and order.SystemModifiedAt is not None:
                failed_at.append(order.SystemModifiedAt)
        await self._advance(orders, failed_at)
        return result

    async def _sync_order(self, order: BCServiceOrderHeader, run_id: str) -> Outcome:
        order_no = order.No
        if not order_no:
            return "VALIDATION_ERROR"
        context = {"run_id": run_id, "service_order_no": order_no}
        existing = await self._states.get_by_order(order_no)
        task: FrontuTask | None = None
        try:
            refs = await self._resolve_refs(order)
            if refs is None:
                logger.warning("Order has no Frontu client match", extra=context)
                return "VALIDATION_ERROR"
            digest = header_hash(order)
            if existing and existing.header_hash == digest and existing.sync_status == "SYNCED":
                task = FrontuTask(guid=existing.frontu_task_guid, code=existing.frontu_task_code)
            else:
                guid = existing.frontu_task_guid if existing else None
                task = await self._frontu.upsert_task(
                    build_task_payload(order, order_no, refs, guid)
                )
                await self._save(order_no, task, digest, "PENDING")
            lines = await self._bc.fetch_order_lines(order_no)
            await self._lines.push_unlinked(order_no, task.guid, lines)
            patch = tasker_patch(task, FALLBACK_STATUS.bc_status, self._now())
            await self._bc.update_order_header(order_no, patch)
            await self._save(order_no, task, digest, "SYNCED")
            return "SYNCED"
        except ExternalServiceError as exc:
            logger.error("Outbound order sync failed: %s", exc, extra=context)
            if task is not None:
                await self._save(order_no, task, header_hash(order), "FAILED", str(exc))
            return "FAILED"

    async def _resolve_refs(self, order: BCServiceOrderHeader) -> FrontuRefs | None:
        if not order.SelltoCustomerNo:
            return None
        client = await self._frontu.find_client_guid(order.SelltoCustomerNo)
        if client is None:
            return None
        workspace = (
            await self._frontu.find_workspace_guid(order.VehicleSerialNo)
            if order.VehicleSerialNo
            else None
        )
        task_type = (
            await self._frontu.find_task_type_guid(order.DealType) if order.DealType else None
        )
        return FrontuRefs(client=client, workspace=workspace, task_type=task_type)

    async def _save(
        self,
        order_no: str,
        task: FrontuTask,
        digest: str,
        status: TaskSyncStatus,
        error: str | None = None,
    ) -> None:
        state = TaskSyncState(
            service_order_no=order_no,
            frontu_task_guid=task.guid,
            frontu_task_code=task.code,
            header_hash=digest,
            origin_system="BC",
            sync_status=status,
            error_message=error[:MAX_ERROR_CHARS] if error else None,
        )
        await self._states.save_task(state)

    async def _advance(self, orders: list[BCServiceOrderHeader], failed_at: list[datetime]) -> None:
        """Move to the earliest failed order, else to the newest order seen."""
        if failed_at:
            target = min(failed_at)
        else:
            seen = [o.SystemModifiedAt for o in orders if o.SystemModifiedAt is not None]
            if not seen:
                return
            target = max(seen)
        current = await self._watermarks.get_raw_watermark(STREAM)
        if current is None or target > current:
            await self._watermarks.set_watermark(STREAM, target)
