"""Headless Module 2 wiring: every task operation runs under the `tasks_sync` lock.

Tasks share the APP_ENV database with the vehicle sync (`sync_states`, `sync_config`,
`sync_audit_log`). The lock has a 15-minute stale cutoff; a fresh holder makes every entry
point return None. Dry-run comes from `sync_config['tasks_sync']` once toggled, else
`SYNC_DRY_RUN` (a user-forced live canary bypasses it); dry-run simulates and audits BC/Frontu
writes and leaves local task state alone. Link health is flushed after every run.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Final, Literal

import httpx
from pydantic import BaseModel

from src.config import Settings
from src.database import get_engine, get_sessionmaker, init_db
from src.domain.canary import CanaryKind, CanaryTarget
from src.repositories.config_repository import SqlConfigStore, resolve_dry_run, runtime_defaults
from src.repositories.sync_repository import SqlSyncRepository
from src.schemas.operations import TriggerKind, mode_of
from src.schemas.sync import AuditEntry, ReleaseStatus
from src.services.link_status import persist_link_health
from src.services.ports import SyncRunResult, utc_now
from src.services.task_outbound import OBJECT_TYPE as ORDER_OBJECT_TYPE
from src.services.task_wiring import Wiring, wire
from src.storage.task_repository import TaskRepository

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 30.0
LOCK_MODULE: Final = "tasks_sync"
# Task passes are short polls; a lock older than this is a crashed run, not a live one.
TASK_STALE_RUN_AFTER = timedelta(minutes=15)

TaskMode = Literal["sync", "outbound", "inbound"]
Direction = Literal["outbound", "inbound"]
# `sync` pushes BC changes first so the inbound pass sees the freshly linked tasks.
MODE_DIRECTIONS: dict[TaskMode, tuple[Direction, ...]] = {
    "sync": ("outbound", "inbound"),
    "outbound": ("outbound",),
    "inbound": ("inbound",),
}
# record_outcomes.object_type of a canary target that failed its run
CANARY_OBJECT_TYPES: dict[CanaryKind, str] = {"order": ORDER_OBJECT_TYPE, "task": "frontu_task"}


class DirectionRun(BaseModel):
    direction: Direction
    result: SyncRunResult
    elapsed_seconds: float


class TaskRunReport(BaseModel):
    dry_run: bool
    runs: list[DirectionRun]


def run_audit_entry(report: TaskRunReport) -> AuditEntry:
    """`TASK_RUN` summary row; the cockpit reads the latest one for its last-run metrics."""
    payload = report.model_dump(mode="json")
    return AuditEntry(entity_key=LOCK_MODULE, action="TASK_RUN", payload=payload)


def build_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)


def task_lock() -> SqlSyncRepository:
    return SqlSyncRepository(get_sessionmaker(), LOCK_MODULE, stale_after=TASK_STALE_RUN_AFTER)


async def _timed(direction: Direction, work: Awaitable[SyncRunResult]) -> DirectionRun:
    started = time.monotonic()
    result = await work
    return DirectionRun(
        direction=direction, result=result, elapsed_seconds=time.monotonic() - started
    )


async def run_tasks(
    settings: Settings, mode: TaskMode, trigger: TriggerKind = "manual"
) -> TaskRunReport | None:
    """Poll the mode's directions; None when another run holds the lock.

    UI and CLI runs are `manual`: no OS schedule registers `tasks` (deploy/register_task.ps1).
    """

    async def work(wiring: Wiring) -> list[DirectionRun]:
        runs = []
        for direction in MODE_DIRECTIONS[mode]:
            await task_lock().heartbeat(utc_now())
            service = wiring.outbound if direction == "outbound" else wiring.inbound
            runs.append(await _timed(direction, service.run()))
        return runs

    return await _report(settings, work, trigger)


async def run_single(
    settings: Settings, target: CanaryTarget, *, force_live: bool = False, force_dry: bool = False
) -> TaskRunReport | None:
    """Canary: one BC order out, or one Frontu task in; watermarks are not moved.

    `force_live` (explicit user choice) bypasses the dry-run wrappers for this run only;
    `force_dry` (the /centras form) makes this run dry whatever the config says, and wins.
    """

    async def work(wiring: Wiring) -> list[DirectionRun]:
        try:
            if target.kind == "order":
                return [await _timed("outbound", wiring.outbound.sync_single_order(target.value))]
            return [await _timed("inbound", wiring.inbound.sync_single_frontu_task(target.value))]
        except Exception as error:  # the run fails as before, with its target as the outcome
            object_type = CANARY_OBJECT_TYPES[target.kind]
            await wiring.recorder.record_failure(object_type, target.value, error)
            raise

    if force_live and not force_dry:
        logger.warning("Forced LIVE task canary for %s %s", target.kind, target.value)
    return await _report(settings, work, "canary", force_live=force_live, force_dry=force_dry)


async def run_bootstrap(settings: Settings) -> int | None:
    """Stamp the Frontu version baseline (even in dry-run: it writes no BC/Frontu data)."""

    async def work(wiring: Wiring) -> int:
        return await wiring.inbound_baseline.bootstrap_watermark()

    return await _locked(settings, work)


async def _report(
    settings: Settings,
    work: Callable[[Wiring], Awaitable[list[DirectionRun]]],
    trigger: TriggerKind,
    *,
    force_live: bool = False,
    force_dry: bool = False,
) -> TaskRunReport | None:
    async def audited(wiring: Wiring) -> TaskRunReport:
        async with wiring.recorder.run(LOCK_MODULE, trigger, mode_of(wiring.dry_run)) as run:
            report = TaskRunReport(dry_run=wiring.dry_run, runs=await work(wiring))
            results = [direction.result for direction in report.runs]
            run.count_ok = sum(r.synced for r in results)
            run.count_skipped = sum(r.skipped for r in results)
            run.count_failed = sum(r.failed + r.validation_errors for r in results)
            await task_lock().record_audit(run_audit_entry(report))
        return report

    return await _locked(settings, audited, force_live=force_live, force_dry=force_dry)


async def _locked[T](
    settings: Settings,
    work: Callable[[Wiring], Awaitable[T]],
    *,
    force_live: bool = False,
    force_dry: bool = False,
) -> T | None:
    await init_db()  # shared tables (locks, config, audit) exist before the claim
    engine = get_engine()
    try:
        await TaskRepository(engine).migrate()
        lock = task_lock()
        now = utc_now()
        if not await lock.try_acquire(now, now):
            logger.warning(
                "Task sync skipped: another run holds the lock", extra={"sync_module": LOCK_MODULE}
            )
            return None
        status: ReleaseStatus = "FAILED"
        try:
            config = SqlConfigStore(get_sessionmaker(), LOCK_MODULE, runtime_defaults(settings))
            dry_run = force_dry or (
                not force_live and await resolve_dry_run(config, settings.sync_dry_run)
            )
            async with build_http_client() as http:
                result = await work(wire(settings, engine, lock, http, dry_run))
            status = "IDLE"
            return result
        finally:
            await lock.release(status, utc_now())
            await persist_link_health(get_sessionmaker())
    finally:
        await engine.dispose()
