"""BC -> Frontu: dispatch `FRONTU` orders as tasks and push their unlinked lines."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from src.adapters.resilience import ExternalServiceError
from src.domain.bc_models import BCServiceOrderHeader
from src.domain.frontu_models import FrontuTask
from src.domain.mapping import FALLBACK_STATUS, header_hash
from src.domain.task_builders import build_task_payload, tasker_patch
from src.domain.task_state import TaskSyncState, TaskSyncStatus
from src.schemas.operations import OutcomeResult
from src.services.error_catalog import rejection_params
from src.services.line_reconciler import LineReconciler
from src.services.ports import (
    MAX_ERROR_CHARS,
    BCTasksPort,
    FrontuTasksPort,
    Outcome,
    OutcomeSink,
    SyncRunResult,
    utc_now,
)
from src.services.task_refs import RefMiss, resolve_refs
from src.storage.task_repository import TaskRepository
from src.storage.task_state_repository import TaskStateRepository

logger = logging.getLogger(__name__)

STREAM = "operative_tasks_bc"
# Only orders BC released for dispatch may be pushed, by the poll or by a canary.
DISPATCH_STATUS = "FRONTU"
# record_outcomes: one row per BC order and run (spec phase 1); "-" = order without a number.
OBJECT_TYPE = "bc_service_order"
NO_NUMBER = "-"
INVALID: Outcome = "VALIDATION_ERROR"
RESULTS: dict[Outcome, OutcomeResult] = {
    "SYNCED": "ok",
    "SKIPPED": "skipped",
    "FAILED": "error",
    "VALIDATION_ERROR": "error",
}


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
        outcomes: OutcomeSink | None = None,
    ) -> None:
        self._bc = bc
        self._outcomes = outcomes
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
            params = {"order_no": order_no}
            result.record(await self._note(order_no, INVALID, "task.order_not_found", params))
        elif order.DocumentStatus != DISPATCH_STATUS:
            logger.warning(
                "Canary: BC order %s has status %r, not %r; nothing pushed",
                order_no,
                order.DocumentStatus,
                DISPATCH_STATUS,
            )
            params = {"status": order.DocumentStatus or ""}
            result.record(await self._note(order_no, "SKIPPED", "task.not_frontu_status", params))
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
            return await self._note(NO_NUMBER, INVALID, "task.no_order_no")
        context = {"run_id": run_id, "service_order_no": order_no}
        existing = await self._states.get_by_order(order_no)
        task: FrontuTask | None = None
        try:
            refs = await resolve_refs(self._frontu, order)
            if isinstance(refs, RefMiss):
                logger.warning("Order reference not found in Frontu: %s", refs.code, extra=context)
                return await self._note(order_no, INVALID, refs.code, refs.params)
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
            return await self._note(order_no, "SYNCED")
        except ExternalServiceError as exc:
            logger.error("Outbound order sync failed: %s", exc, extra=context)
            if task is not None:
                await self._save(order_no, task, header_hash(order), "FAILED", str(exc))
            params = rejection_params(exc)
            return await self._note(order_no, "FAILED", params=params, error=exc)

    async def _note(
        self,
        order_no: str,
        outcome: Outcome,
        code: str | None = None,
        params: dict[str, str] | None = None,
        error: BaseException | None = None,
    ) -> Outcome:
        """Record the order's outcome in the current job run (if wired); returns `outcome`."""
        if self._outcomes is not None:
            await self._outcomes.record_outcome(
                OBJECT_TYPE, order_no, RESULTS[outcome], error_code=code, params=params, error=error
            )
        return outcome

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
