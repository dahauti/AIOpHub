"""Background scheduling for the vehicle pipeline: periodic daemon, on-demand trigger, abort."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from src.adapters.resilience import Sleep
from src.pipelines.ports import utc_now
from src.pipelines.sync_vehicles import MODULE
from src.repositories.config_repository import RuntimeConfigStore
from src.repositories.sync_repository import SyncRepository
from src.schemas.sync import (
    RUNNING,
    AuditEntry,
    ConfigFlag,
    DashboardSnapshot,
    RuntimeConfig,
    SyncRunResult,
)

logger = logging.getLogger(__name__)

RECENT_AUDIT_LIMIT = 15


class RunnablePipeline(Protocol):
    @property
    def is_running(self) -> bool: ...
    async def run(self) -> SyncRunResult | None: ...


@runtime_checkable
class SyncControl(Protocol):
    """Port consumed by the dashboard routes."""

    def trigger(self) -> bool: ...
    async def snapshot(self) -> DashboardSnapshot: ...
    async def set_cursor(self, cursor: datetime) -> bool: ...
    async def config(self) -> RuntimeConfig: ...
    async def toggle(self, flag: ConfigFlag) -> RuntimeConfig | None: ...
    async def abort(self) -> bool: ...


class SyncRunner:
    """Owns background runs: the periodic daemon loop and manually triggered runs."""

    def __init__(
        self,
        pipeline: RunnablePipeline,
        repository: SyncRepository,
        config: RuntimeConfigStore,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._pipeline = pipeline
        self._repo = repository
        self._config = config
        self._clock = clock
        self._tasks: set[asyncio.Task[SyncRunResult | None]] = set()

    @property
    def _busy(self) -> bool:
        return self._pipeline.is_running or bool(self._tasks)

    def start_daemon(self, interval_seconds: float) -> asyncio.Task[None]:
        """Start the periodic loop; each tick runs only while auto-sync is enabled."""
        return asyncio.create_task(self.run_forever(interval_seconds), name="vehicle-sync-daemon")

    def trigger(self) -> bool:
        """Start a background run unless one is active; returns whether a run was started."""
        if self._busy:
            return False
        self._start("vehicle-sync-manual").add_done_callback(self._on_manual_done)
        return True

    async def set_cursor(self, cursor: datetime) -> bool:
        """Manually move the sync cursor; refused (False) while any run is active."""
        if self._busy:
            return False
        return await self._repo.set_cursor(cursor, self._clock())

    async def config(self) -> RuntimeConfig:
        return await self._config.get()

    async def toggle(self, flag: ConfigFlag) -> RuntimeConfig | None:
        """Flip a runtime flag; the run mode is locked (None) while a run is active."""
        if flag == "dry_run" and self._busy:
            return None
        config = await self._config.toggle(flag)
        logger.warning("Runtime config changed", extra=config.model_dump())
        return config

    async def abort(self) -> bool:
        """Cancel the active run, release the module to IDLE and audit ABORTED.

        Also clears a RUNNING claim with no in-process run (crashed or external run).
        """
        tasks = list(self._tasks)
        if not tasks and (await self.snapshot()).status != RUNNING:
            return False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._repo.release("IDLE", self._clock())
        await self._repo.record_audit(AuditEntry(entity_key=MODULE, action="ABORTED"))
        logger.warning("Sync aborted from the dashboard", extra={"sync_module": MODULE})
        return True

    async def snapshot(self) -> DashboardSnapshot:
        """Dashboard state with counters measured from today's UTC midnight."""
        day_start = self._clock().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return await self._repo.dashboard_snapshot(day_start, RECENT_AUDIT_LIMIT)

    async def run_forever(self, interval_seconds: float, *, sleep: Sleep = asyncio.sleep) -> None:
        """Tick immediately, then every `interval_seconds`; a failed run never stops the loop."""
        while True:
            try:
                await self._scheduled_run()
            except Exception:
                logger.exception("Scheduled sync run failed")
            await sleep(interval_seconds)

    async def shutdown(self) -> None:
        """Cancel and await any in-flight runs."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _scheduled_run(self) -> None:
        if not (await self._config.get()).auto_sync_enabled:
            logger.debug("Auto-sync is disabled; scheduled run skipped")
            return
        if self._busy:
            return
        task = self._start("vehicle-sync-scheduled")
        try:
            result = await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if not task.cancelled() or (current is not None and current.cancelling()):
                raise
            return  # aborted from the dashboard; the loop keeps its schedule
        if result is not None:
            logger.info("Scheduled sync run finished", extra=result.model_dump(mode="json"))

    def _start(self, name: str) -> asyncio.Task[SyncRunResult | None]:
        task = asyncio.create_task(self._pipeline.run(), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _on_manual_done(self, task: asyncio.Task[SyncRunResult | None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Manual sync run failed", exc_info=error)
