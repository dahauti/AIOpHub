"""Frontu-born tasks: create the BC Service Order, ingest lines, write `SOxxxxx` back."""

import logging

from src.domain.frontu_models import FrontuTask
from src.domain.mapping import header_hash
from src.domain.task_builders import build_order_from_task, build_writeback_payload
from src.domain.task_state import (
    TaskSyncState,
    TaskSyncStatus,
    is_unlinked_order_key,
    unlinked_order_key,
)
from src.services.line_reconciler import LineReconciler
from src.services.ports import MAX_ERROR_CHARS, BCTasksPort, FrontuTasksPort, Outcome
from src.storage.task_state_repository import TaskStateRepository

logger = logging.getLogger(__name__)


class FrontuBornHandler:
    """BC requires `SelltoCustomerNo`; it is resolved from the Frontu client's `systemId`."""

    def __init__(
        self,
        bc: BCTasksPort,
        frontu: FrontuTasksPort,
        states: TaskStateRepository,
        lines: LineReconciler,
    ) -> None:
        self._bc = bc
        self._frontu = frontu
        self._states = states
        self._lines = lines

    async def sync(self, task: FrontuTask, context: dict[str, str]) -> Outcome:
        if task.client is None:
            return await self._reject(task, "Frontu task has no client", context)
        state = await self._states.get_by_task_guid(task.guid)
        if state is not None and not is_unlinked_order_key(state.service_order_no):
            order_no, digest = state.service_order_no, state.header_hash
        else:
            customer_no = await self._frontu.get_client_system_id(task.client)
            if customer_no is None:
                detail = f"Frontu client {task.client} has no BC customer systemId"
                return await self._reject(task, detail, context)
            header = build_order_from_task(task, customer_no)
            order_no = await self._bc.create_service_order(header)
            if state is not None:
                await self._states.delete_task(state.service_order_no)
            digest = header_hash(header.model_copy(update={"No": order_no}))
            await self._save(order_no, task, digest, "PENDING")
        await self._lines.ingest(order_no, task.guid)
        await self._frontu.upsert_task(build_writeback_payload(task, order_no, task.client))
        await self._save(order_no, task, digest, "SYNCED")
        return "SYNCED"

    async def _reject(self, task: FrontuTask, detail: str, context: dict[str, str]) -> Outcome:
        """Record VALIDATION_ERROR without touching BC; unlinked tasks get a placeholder key."""
        logger.warning("Frontu-born task rejected: %s", detail, extra=context)
        state = await self._states.get_by_task_guid(task.guid)
        if state is not None:
            order_no, digest = state.service_order_no, state.header_hash
        else:
            order_no = unlinked_order_key(task.guid)
            digest = header_hash(build_order_from_task(task, ""))
        await self._save(order_no, task, digest, "VALIDATION_ERROR", detail)
        return "VALIDATION_ERROR"

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
            origin_system="FRONTU",
            sync_status=status,
            error_message=error[:MAX_ERROR_CHARS] if error else None,
        )
        await self._states.save_task(state)
