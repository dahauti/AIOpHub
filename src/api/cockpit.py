"""Cockpit-wide template context: task telemetry, environment, per-link health, status."""

from typing import Literal

from fastapi import Request

from src.schemas.sync import RUNNING, DashboardSnapshot
from src.services.cockpit_models import TaskCockpit
from src.services.debug_console import DebugConsolePort, DebugEvent
from src.services.link_status import LinkPanel, LinkPanelPort
from src.services.pipeline_view import pipeline_nodes
from src.services.task_control import STALE, TaskControlPort

SystemStatus = Literal["OPERATIONAL", "SYNCING", "DEGRADED"]
FAILED = "FAILED"


def system_status(vehicles: str, task: TaskCockpit | None) -> SystemStatus:
    """DEGRADED on any failed/stale stream, else SYNCING while either stream runs."""
    task_status = task.status if task is not None else None
    if vehicles == FAILED or task_status in (FAILED, STALE):
        return "DEGRADED"
    if vehicles == RUNNING or (task is not None and task.running):
        return "SYNCING"
    return "OPERATIONAL"


async def link_panel(request: Request) -> LinkPanel:
    """Per-service link health; all UNKNOWN when no reader is wired (vehicles-only app)."""
    reader = getattr(request.app.state, "link_health", None)
    return await reader.panel() if isinstance(reader, LinkPanelPort) else LinkPanel()


def task_control(request: Request) -> TaskControlPort | None:
    """The task port when the lifespan wired one (a vehicles-only app renders without it)."""
    control = getattr(request.app.state, "task_control", None)
    return control if isinstance(control, TaskControlPort) else None


async def debug_events(request: Request) -> list[DebugEvent]:
    """Glass Box console rows; empty when no console is wired."""
    console = getattr(request.app.state, "debug_console", None)
    return await console.events() if isinstance(console, DebugConsolePort) else []


async def cockpit_context(request: Request, snapshot: DashboardSnapshot) -> dict[str, object]:
    control = task_control(request)
    task = await control.cockpit() if control is not None else None
    links = await link_panel(request)
    return {
        "task": task,
        "app_env": task.app_env if task is not None else None,
        "system_status": system_status(snapshot.status, task),
        "links": links,
        "pipeline": pipeline_nodes(task, links),
    }
