"""Frontu -> BC: status transitions, labor/material ingest and Frontu-born Service Orders."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from src.adapters.resilience import ExternalServiceError
from src.domain.frontu_models import FrontuTask
from src.domain.mapping import (
    frontu_code_for_bc_status,
    header_hash,
    is_frozen,
    is_line_update_allowed,
    map_frontu_status,
)
from src.domain.task_builders import tasker_patch
from src.domain.task_state import OriginSystem, TaskSyncState, TaskSyncStatus
from src.services.frontu_born import FrontuBornHandler
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

STREAM = "operative_tasks_frontu"
# Without a stored watermark, poll only the last 24h instead of the whole task history.
COLD_START_LOOKBACK_SECONDS = 86400


class TaskInboundService:
    """One polling pass over the Frontu task delta; the watermark never passes a failed task."""

    def __init__(
        self,
        bc: BCTasksPort,
        frontu: FrontuTasksPort,
        watermarks: TaskRepository,
        states: TaskStateRepository,
        *,
        initial_version: int | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._bc = bc
        self._frontu = frontu
        self._watermarks = watermarks
        self._states = states
        self._lines = LineReconciler(bc, frontu, states)
        self._born = FrontuBornHandler(bc, frontu, states, self._lines)
        self._initial = initial_version
        self._now = now

    def cold_start_version(self) -> int:
        """`initial_version` when given, else now - 24h as a Unix-seconds version floor."""
        if self._initial is not None:
            return self._initial
        return int(self._now().timestamp()) - COLD_START_LOOKBACK_SECONDS

    async def _since(self) -> int:
        stored = await self._watermarks.get_version_watermark(STREAM)
        if stored is not None:
            return stored
        floor = self.cold_start_version()
        logger.warning("No %s watermark: cold start from version %d", STREAM, floor)
        return floor

    async def bootstrap_watermark(self) -> int:
        """Stamp the newest remote version as the baseline without syncing any history."""
        floor = await self._since()
        latest = await self._frontu.scan_latest_version(floor)
        baseline = floor if latest is None else max(latest, floor)
        await self._watermarks.set_version_watermark(STREAM, baseline)
        return baseline

    async def sync_single_frontu_task(self, task_guid: str) -> SyncRunResult:
        """Canary: pull one Frontu task into BC; the stream watermark is left untouched."""
        result = SyncRunResult()
        task = await self._frontu.get_task(task_guid)
        if task is None:
            logger.warning("Canary: Frontu task %s not found", task_guid)
            result.record("VALIDATION_ERROR")
        else:
            result.record(await self._sync_task(task, uuid.uuid4().hex))
        return result

    async def run(self) -> SyncRunResult:
        run_id = uuid.uuid4().hex
        since = await self._since()
        tasks = await self._frontu.fetch_task_delta(since)
        result = SyncRunResult()
        failed: list[int] = []
        for task in tasks:
            outcome = await self._sync_task(task, run_id)
            result.record(outcome)
            if outcome == "FAILED" and task.version is not None:
                failed.append(task.version)
        versions = [task.version for task in tasks if task.version is not None]
        # Stop just below the earliest failure so it is re-polled under `>` or `>=` semantics.
        target = min(failed) - 1 if failed else max(versions, default=since)
        if target > since:
            await self._watermarks.set_version_watermark(STREAM, target)
        return result

    async def _sync_task(self, task: FrontuTask, run_id: str) -> Outcome:
        context = {"run_id": run_id, "frontu_task_guid": task.guid}
        order_no = (task.systemId or "").strip()
        try:
            if order_no:
                return await self._sync_linked(task, order_no, context)
            return await self._born.sync(task, context)
        except ExternalServiceError as exc:
            logger.error("Inbound task sync failed: %s", exc, extra=context)
            state = await self._states.get_by_task_guid(task.guid)
            if state is not None:
                error = str(exc)[:MAX_ERROR_CHARS]
                await self._states.save_task(
                    state.model_copy(update={"sync_status": "FAILED", "error_message": error})
                )
            return "FAILED"

    async def _sync_linked(
        self, task: FrontuTask, order_no: str, context: dict[str, str]
    ) -> Outcome:
        order = await self._bc.fetch_order(order_no)
        if order is None:
            logger.warning("Linked BC order %s not found", order_no, extra=context)
            return "VALIDATION_ERROR"
        current = frontu_code_for_bc_status(order.DocumentStatus)
        if is_frozen(current):
            return "SKIPPED"
        if current is None or is_line_update_allowed(current):
            await self._lines.ingest(order_no, task.guid)
        patch = tasker_patch(task, map_frontu_status(task.status), self._now())
        await self._bc.update_order_header(order_no, patch)
        existing = await self._states.get_by_order(order_no)
        digest = existing.header_hash if existing else header_hash(order)
        origin: OriginSystem = existing.origin_system if existing else "BC"
        await self._save(order_no, task, digest, origin, "SYNCED")
        return "SYNCED"

    async def _save(
        self,
        order_no: str,
        task: FrontuTask,
        digest: str,
        origin: OriginSystem,
        status: TaskSyncStatus,
    ) -> None:
        state = TaskSyncState(
            service_order_no=order_no,
            frontu_task_guid=task.guid,
            frontu_task_code=task.code,
            header_hash=digest,
            origin_system=origin,
            sync_status=status,
        )
        await self._states.save_task(state)
