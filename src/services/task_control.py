"""Cockpit port for the task sync: background trigger, dry-run toggle and card telemetry.

A UI-triggered run executes `run_tasks` as an asyncio task, so the request returns at once;
the `tasks_sync` lock in `sync_states` still guards against overlap with CLI runs.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from src.config import Settings
from src.database import get_engine, get_sessionmaker
from src.domain.canary import CanaryTarget
from src.logging_setup import failed_run_trace
from src.repositories.config_repository import SqlConfigStore, runtime_defaults
from src.repositories.event_repository import AuditEvent, AuditEventRepository
from src.repositories.task_status_repository import TaskStatusRepository
from src.schemas.sync import RUNNING, RuntimeConfig
from src.services.cockpit_models import CanaryOutcome, LastRun, TaskCockpit
from src.services.ports import utc_now
from src.services.task_inbound import STREAM as FRONTU_STREAM
from src.services.task_outbound import STREAM as BC_STREAM
from src.services.task_runner import (
    LOCK_MODULE,
    TASK_STALE_RUN_AFTER,
    TaskMode,
    TaskRunReport,
    run_single,
    run_tasks,
)
from src.storage.task_repository import TaskRepository

logger = logging.getLogger(__name__)

TaskRunner = Callable[[Settings, TaskMode], Awaitable[TaskRunReport | None]]


class SingleRunner(Protocol):
    def __call__(
        self,
        settings: Settings,
        target: CanaryTarget,
        /,
        *,
        force_live: bool = False,
        force_dry: bool = False,
    ) -> Awaitable[TaskRunReport | None]: ...


NEVER_RUN = "NEVER RUN"
STALE = "STALE"


@runtime_checkable
class TaskControlPort(Protocol):
    """Port consumed by the task cockpit routes."""

    def trigger(self) -> bool: ...
    def trigger_single(
        self, target: CanaryTarget, force_live: bool = False, force_dry: bool = False
    ) -> bool: ...
    async def cockpit(self) -> TaskCockpit: ...
    async def toggle_dry_run(self) -> RuntimeConfig | None: ...


class TaskSyncControl:
    """In-process trigger plus reads of the shared `tasks_sync` state and config."""

    def __init__(
        self,
        settings: Settings,
        runner: TaskRunner = run_tasks,
        single_runner: SingleRunner = run_single,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self._single_runner = single_runner
        self._task: asyncio.Task[None] | None = None
        self._canary: CanaryOutcome | None = None
        sessions = get_sessionmaker()
        self._status = TaskStatusRepository(sessions, LOCK_MODULE)
        self._config = SqlConfigStore(sessions, LOCK_MODULE, runtime_defaults(settings))
        self._events = AuditEventRepository(sessions)
        self._watermarks = TaskRepository(get_engine(), settings.task_safety_buffer_seconds)

    async def prepare(self) -> None:
        """Create the task tables so the cockpit can read watermarks before the first run."""
        await self._watermarks.migrate()

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def trigger(self) -> bool:
        """Start an outbound + inbound pass in the background; False if one is in flight."""
        if self.busy:
            return False
        self._task = asyncio.create_task(self._run())
        return True

    def trigger_single(
        self, target: CanaryTarget, force_live: bool = False, force_dry: bool = False
    ) -> bool:
        """Start a one-entity canary in the background; False if any UI run is in flight.

        `force_live` bypasses the tasks_sync dry-run for this canary only; `force_dry`
        (the /centras form) makes it dry regardless and wins over `force_live`.
        """
        if self.busy:
            return False
        self._canary = CanaryOutcome(target=target, state="RUNNING")
        self._task = asyncio.create_task(self._run_single(target, force_live, force_dry))
        return True

    async def cockpit(self) -> TaskCockpit:
        state = await self._status.stream_state()
        status = NEVER_RUN if state is None else state.status
        stale_before = utc_now() - TASK_STALE_RUN_AFTER
        if state is not None and status == RUNNING and state.updated_at < stale_before:
            status = STALE
        return TaskCockpit(
            status=status,
            updated_at=None if state is None else state.updated_at,
            running=self.busy or status == RUNNING,
            dry_run=(await self._config.get()).dry_run,
            app_env=self._settings.app_env,
            last_run=await self._last_run(),
            bc_watermark=await self._watermarks.get_raw_watermark(BC_STREAM),
            frontu_version=await self._watermarks.get_version_watermark(FRONTU_STREAM),
            canary=self._canary,
            frontu_base_url=self._settings.active_frontu_base_url,
            bc_company=self._settings.active_bc_company,
            bc_profile=self._settings.bc_active_profile,
        )

    async def toggle_dry_run(self) -> RuntimeConfig | None:
        """Flip `sync_config['tasks_sync'].dry_run`; None while a run is in progress."""
        if (await self.cockpit()).running:
            return None
        return await self._config.toggle("dry_run")

    async def shutdown(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        try:
            if await self._runner(self._settings, "sync") is None:
                logger.warning("UI task sync skipped: another run holds the tasks_sync lock")
        except Exception as exc:
            with failed_run_trace(exc):
                logger.exception("UI-triggered task sync failed")

    async def _run_single(self, target: CanaryTarget, force_live: bool, force_dry: bool) -> None:
        started = utc_now().replace(microsecond=0)  # audit rows are stamped to the second
        try:
            report = await self._single_runner(
                self._settings, target, force_live=force_live, force_dry=force_dry
            )
        except Exception as exc:
            with failed_run_trace(exc):
                logger.exception("Task canary for %s failed", target.value)
            self._canary = CanaryOutcome.from_error(target, exc)
            return
        if report is None:
            self._canary = CanaryOutcome(target=target, state="BUSY")
        else:
            writes = await self._simulated_writes(started) if report.dry_run else []
            self._canary = CanaryOutcome.from_report(target, report, writes)

    async def _simulated_writes(self, started: datetime) -> list[AuditEvent]:
        """DRY_RUN_TASK rows written since the canary started, for display only.

        The window starts at the start second (audit rows are stamped to the second) and is
        read after the lock is released, so rows of an adjacent task run may be included.
        """
        try:
            return await self._events.since(started, "DRY_RUN_TASK")
        except SQLAlchemyError:
            logger.warning("Canary step detail unavailable: audit log unreadable", exc_info=True)
            return []

    async def _last_run(self) -> LastRun | None:
        entry = await self._status.latest_run()
        if entry is None:
            return None
        try:
            report = TaskRunReport.model_validate(entry.payload)
        except ValidationError:
            logger.warning("Ignoring unreadable TASK_RUN payload")
            return None
        return LastRun.from_report(entry.created_at, report)
