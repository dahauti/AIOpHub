"""Unified canary bar: one form for a Service Order/Frontu task or a vehicle (HTTP only).

Vehicles reuse the single-vehicle route (synchronous, one record). Orders and tasks start
the background task canary; its row is refreshed out of band. The "force live" checkbox
bypasses dry-run for that single record in both modules, exactly as the user chose.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse

from src.api.cockpit import task_control
from src.api.routes_ui import Control, get_single_sync, sync_vehicle
from src.api.task_handlers import canary_banner
from src.api.templating import TEMPLATES
from src.domain.canary import parse_canary_target

TASKS_UNAVAILABLE = "Task sync is not wired in this app instance"
MAX_RECORD_ID = 128

canary_router = APIRouter()


async def _task_result(
    request: Request, message: str, status_code: int = 200, *, refresh_card: bool = False
) -> HTMLResponse:
    control = task_control(request)
    task = await control.cockpit() if refresh_card and control is not None else None
    context: dict[str, object] = {"single_banner": message, "task": task, "task_oob": True}
    return TEMPLATES.TemplateResponse(
        request, "partials/canary_task_result.html", context, status_code=status_code
    )


@canary_router.post("/api/canary", response_class=HTMLResponse)
async def run_canary(
    request: Request,
    control: Control,
    record_id: Annotated[str, Form(max_length=MAX_RECORD_ID)] = "",
    target_module: Annotated[Literal["order", "vehicle"], Form()] = "order",
    force_live: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    if target_module == "vehicle":
        single = get_single_sync(request)  # resolved here: order canaries never need it
        return await sync_vehicle(request, control, single, record_id, force_live)
    unprocessable = status.HTTP_422_UNPROCESSABLE_CONTENT
    tasks = task_control(request)
    if tasks is None:
        return await _task_result(request, TASKS_UNAVAILABLE, status.HTTP_409_CONFLICT)
    try:
        target = parse_canary_target(record_id)
    except ValueError as exc:
        return await _task_result(request, str(exc), unprocessable)
    started = tasks.trigger_single(target, force_live)
    dry_run = (await tasks.cockpit()).dry_run
    banner = canary_banner(target, started, dry_run, force_live=force_live)
    return await _task_result(request, banner, refresh_card=True)
