"""Task sync cockpit card: telemetry, dry-run toggle and background trigger (HTTP only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse

from src.api.routes_ui import STOP_POLLING, SYNC_STATE_CHANGED
from src.api.templating import TEMPLATES
from src.domain.canary import MAX_TARGET_CHARS, CanaryTarget, parse_canary_target
from src.schemas.sync import RuntimeConfig
from src.services.task_control import TaskControlPort

TASKS_STARTED = "Task sync started"
TASKS_ALREADY_RUNNING = "Task sync already in progress"
TASKS_DRY_RUN_NOTE = " (dry run: no changes will be sent to BC or Frontu)"
TASKS_FORCED_LIVE_NOTE = " (forced LIVE: writes to BC and Frontu)"
TASK_MODE_LOCKED = "Task sync is running: the mode cannot be changed until it finishes"


def get_task_control(request: Request) -> TaskControlPort:
    control = request.app.state.task_control
    if not isinstance(control, TaskControlPort):
        raise RuntimeError("Task control is not initialised; is the app lifespan running?")
    return control


TaskControl = Annotated[TaskControlPort, Depends(get_task_control)]
task_router = APIRouter()


def canary_banner(
    target: CanaryTarget, started: bool, dry_run: bool, *, force_live: bool = False
) -> str:
    if not started:
        return TASKS_ALREADY_RUNNING
    label = "BC order" if target.kind == "order" else "Frontu task"
    note = TASKS_FORCED_LIVE_NOTE if force_live else TASKS_DRY_RUN_NOTE if dry_run else ""
    return f"Canary started for {label} {target.value}{note}"


def task_toggle_banner(config: RuntimeConfig) -> str:
    if config.dry_run:
        return "Task mode set to DRY RUN: no changes will be sent to BC or Frontu"
    return "Task mode set to LIVE: the next task run writes to BC and Frontu"


async def task_card(
    request: Request,
    control: TaskControlPort,
    banner: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context: dict[str, object] = {"task": await control.cockpit(), "task_banner": banner}
    return TEMPLATES.TemplateResponse(
        request, "partials/task_card.html", context, status_code=status_code
    )


@task_router.get("/partials/tasks", response_class=HTMLResponse)
async def task_card_partial(request: Request, control: TaskControl) -> HTMLResponse:
    """Card render for the initial page load."""
    return await task_card(request, control)


@task_router.get("/partials/tasks/poll", response_class=HTMLResponse)
async def task_card_poll(request: Request, control: TaskControl) -> HTMLResponse:
    """Poll target while running; once idle a final card (286) that also refreshes the audit."""
    response = await task_card(request, control)
    if not (await control.cockpit()).running:
        response.status_code = STOP_POLLING
        response.headers["HX-Trigger"] = SYNC_STATE_CHANGED
    return response


@task_router.post("/api/tasks/trigger", response_class=HTMLResponse)
async def trigger_task_sync(request: Request, control: TaskControl) -> HTMLResponse:
    """Starts the run in the background and returns at once with a self-polling card."""
    banner = TASKS_STARTED if control.trigger() else TASKS_ALREADY_RUNNING
    if (await control.cockpit()).dry_run:
        banner += TASKS_DRY_RUN_NOTE
    response = await task_card(request, control, banner)
    response.headers["HX-Trigger"] = SYNC_STATE_CHANGED  # header badges follow the new run
    return response


@task_router.post("/api/tasks/single", response_class=HTMLResponse)
async def trigger_task_canary(
    request: Request,
    control: TaskControl,
    target: Annotated[str, Form(max_length=MAX_TARGET_CHARS + 16)] = "",
) -> HTMLResponse:
    """One SO No (BC -> Frontu) or Frontu task GUID (Frontu -> BC), run in the background."""
    try:
        parsed = parse_canary_target(target)
    except ValueError as exc:
        return await task_card(request, control, str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT)
    started = control.trigger_single(parsed)
    banner = canary_banner(parsed, started, (await control.cockpit()).dry_run)
    response = await task_card(request, control, banner)
    response.headers["HX-Trigger"] = SYNC_STATE_CHANGED
    return response


@task_router.post("/partials/tasks/toggle-dry-run", response_class=HTMLResponse)
async def toggle_task_dry_run(request: Request, control: TaskControl) -> HTMLResponse:
    config = await control.toggle_dry_run()
    if config is None:
        return await task_card(request, control, TASK_MODE_LOCKED, status.HTTP_409_CONFLICT)
    return await task_card(request, control, task_toggle_banner(config))
