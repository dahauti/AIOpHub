"""Composition of the task services for one locked run (live or dry-run ports and stores)."""

from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.bc_client import select_connection
from src.clients.bc_tasks_client import BCTasksClient
from src.clients.frontu_tasks_client import FrontuTasksClient
from src.config import Settings
from src.services.dry_run import (
    AuditSink,
    DryRunBC,
    DryRunFrontu,
    ReadOnlyStateRepository,
    ReadOnlyTaskRepository,
)
from src.services.ports import BCTasksPort, FrontuTasksPort, utc_now
from src.services.task_inbound import TaskInboundService
from src.services.task_outbound import TaskOutboundService
from src.storage.task_repository import TaskRepository
from src.storage.task_state_repository import TaskStateRepository


@dataclass(frozen=True)
class Wiring:
    """Services for one locked run; `inbound_baseline` always writes the real watermark."""

    dry_run: bool
    outbound: TaskOutboundService
    inbound: TaskInboundService
    inbound_baseline: TaskInboundService


def wire(
    settings: Settings,
    engine: AsyncEngine,
    audit: AuditSink,
    http: httpx.AsyncClient,
    dry_run: bool,
) -> Wiring:
    buffer = settings.task_safety_buffer_seconds
    watermarks = (ReadOnlyTaskRepository if dry_run else TaskRepository)(engine, buffer)
    states = (ReadOnlyStateRepository if dry_run else TaskStateRepository)(engine)
    bc: BCTasksPort = BCTasksClient(http, select_connection(settings))
    frontu: FrontuTasksPort = FrontuTasksClient(
        http, settings.active_frontu_base_url, settings.active_frontu_api_key
    )
    if dry_run:
        bc, frontu = DryRunBC(bc, audit), DryRunFrontu(frontu, audit)
    initial = utc_now() - timedelta(days=settings.sync_initial_lookback_days)
    outbound = TaskOutboundService(bc, frontu, watermarks, states, initial_watermark=initial)
    baseline = TaskInboundService(bc, frontu, TaskRepository(engine, buffer), states)
    return Wiring(
        dry_run=dry_run,
        outbound=outbound,
        inbound=TaskInboundService(bc, frontu, watermarks, states),
        inbound_baseline=baseline,
    )
