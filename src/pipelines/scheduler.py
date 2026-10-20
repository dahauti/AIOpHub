"""Background scheduling for the vehicle pipeline: periodic daemon and on-demand trigger."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from src.adapters.resilience import Sleep
from src.pipelines.ports import utc_now
from src.repositories.sync_repository import SyncRepository
from src.schemas.sync import DashboardSnapshot, SyncRunResult

logger = logging.getLogger(__name__)

RECENT_AUDIT_LIMIT = 15


class RunnablePipeline(Protocol):
    @property
    def is_running(self) -> bool: ...
    @property
    def dry_run(self) -> bool: ...
    async def run(self) -> SyncRunResult | None: ...


@runtime_checkable
class SyncControl(Protocol):
    """Port consumed by the dashboard routes."""

    @property
    def dry_run(self) -> bool: ...
    def trigger(self) -> bool: ...
    async def snapshot(self) -> DashboardSnapshot: ...
    async def set_cursor(self, cursor: datetime) -> bool: ...


class SyncRunner:
    """Owns background runs: the periodic daemon loop and manually triggered runs."""

    def __init__(
        self,
        pipeline: RunnablePipeline,
        repository: SyncRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._pipeline = pipeline
        self._repo = repository
        self._clock = clock
        self._tasks: set[asyncio.Task[SyncRunResult | None]] = set()

    @property
    def dry_run(self) -> bool:
        return self._pipeline.dry_run

    def start_daemon(self, *, enabled: bool, interval_seconds: float) -> asyncio.Task[None] | None:
        """Start the periodic loop, or log and return None when disabled by config."""
        if not enabled:
            logger.warning("Background sync daemon is disabled by config")
            return None
        return asyncio.create_task(self.run_forever(interval_seconds), name="vehicle-sync-daemon")

    def trigger(self) -> bool:
        """Start a background run unless one is active; returns whether a run was started."""
        if self._pipeline.is_running or self._tasks:
            return False
        task = asyncio.create_task(self._pipeline.run(), name="vehicle-sync-manual")
        self._tasks.add(task)
        task.add_done_callback(self._on_manual_done)
        return True

    async def set_cursor(self, cursor: datetime) -> bool:
        """Manually move the sync cursor; refused (False) while any run is active."""
        if self._pipeline.is_running or self._tasks:
            return False
        return await self._repo.set_cursor(cursor, self._clock())

    async def snapshot(self) -> DashboardSnapshot:
        """Dashboard state with counters measured from today's UTC midnight."""
        day_start = self._clock().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return await self._repo.dashboard_snapshot(day_start, RECENT_AUDIT_LIMIT)

    async def run_forever(self, interval_seconds: float, *, sleep: Sleep = asyncio.sleep) -> None:
        """Run immediately, then every `interval_seconds`; a failed run never stops the loop."""
        while True:
            try:
                result = await self._pipeline.run()
            except Exception:
                logger.exception("Scheduled sync run failed")
            else:
                if result is not None:
                    logger.info("Scheduled sync run finished", extra=result.model_dump(mode="json"))
            await sleep(interval_seconds)

    async def shutdown(self) -> None:
        """Cancel and await any in-flight manual runs."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _on_manual_done(self, task: asyncio.Task[SyncRunResult | None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Manual sync run failed", exc_info=error)
