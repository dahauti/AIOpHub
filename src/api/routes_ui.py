"""HTMX dashboard routes: HTTP transport only, backed by the SyncControl port."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, Response

from src.api.cursor_handlers import CURSOR_LOCKED, MAX_CURSOR_INPUT, CursorInputError, parse_cursor
from src.api.templating import TEMPLATES, format_utc
from src.pipelines.scheduler import SyncControl
from src.pipelines.single_vehicle import SingleVehicleControl
from src.schemas.sync import RUNNING, SingleSyncResult

SYNC_STARTED = "Sync started"
SYNC_ALREADY_RUNNING = "Sync already in progress"
DRY_RUN_NOTE = " (dry run: no changes will be sent to Frontu)"
SINGLE_BUSY = "Sync already in progress. Please wait for the current batch to complete."
BLANK_SERIAL = "Enter a vehicle SerialNo"
# htmx swaps a 286 response, then cancels the polling element's timer.
STOP_POLLING = 286
SYNC_STATE_CHANGED = "sync-state-changed"


def get_sync_control(request: Request) -> SyncControl:
    control = request.app.state.sync_control
    if not isinstance(control, SyncControl):
        raise RuntimeError("Sync control is not initialised; is the app lifespan running?")
    return control


def get_single_sync(request: Request) -> SingleVehicleControl:
    single = request.app.state.single_sync
    if not isinstance(single, SingleVehicleControl):
        raise RuntimeError("Single-vehicle sync is not initialised; is the app lifespan running?")
    return single


def single_sync_banner(result: SingleSyncResult) -> str:
    """User-facing outcome line for a targeted single-vehicle sync."""
    serial = result.serial_no
    match result.outcome:
        case "SYNCED":
            archived = " (archived)" if result.action == "SINGLE_ARCHIVE" else ""
            return f"Synced vehicle {serial}{archived}: HTTP {result.status_code}"
        case "DRY_RUN":
            return f"Dry run for {serial}: no changes sent to Frontu"
        case "NOT_FOUND":
            return f"Vehicle {serial} not found in BC"
        case "BUSY":
            return SINGLE_BUSY
        case "FAILED":
            return f"Sync of {serial} failed: {result.error}"


Control = Annotated[SyncControl, Depends(get_sync_control)]
Single = Annotated[SingleVehicleControl, Depends(get_single_sync)]
router = APIRouter()


async def _context(control: SyncControl, banner: str | None, **extra: object) -> dict[str, object]:
    """Template context; the KPI block polls only while the snapshot says RUNNING."""
    snapshot, config = await control.snapshot(), await control.config()
    base: dict[str, object] = {
        "snapshot": snapshot,
        "banner": banner,
        "config": config,
        "dry_run": config.dry_run,
        "auto_sync": config.auto_sync_enabled,
        "poll": snapshot.status == RUNNING,
        "next_sync": await control.next_sync(),
    }
    return base | extra


async def status_response(
    request: Request,
    control: SyncControl,
    banner: str | None,
    status_code: int = 200,
    *,
    force_poll: bool = False,
) -> HTMLResponse:
    context = await _context(control, banner)
    context["poll"] = force_poll or context["poll"]
    return TEMPLATES.TemplateResponse(
        request, "partials/status.html", context, status_code=status_code
    )


async def _apply_cursor(request: Request, control: SyncControl, cursor: datetime) -> HTMLResponse:
    if not await control.set_cursor(cursor):
        return await status_response(request, control, CURSOR_LOCKED, status.HTTP_409_CONFLICT)
    return await status_response(request, control, f"Cursor set to {format_utc(cursor)}")


async def _vehicle_result(
    request: Request, control: SyncControl, message: str, single: SingleSyncResult | None
) -> HTMLResponse:
    """Canary card content, plus an out-of-band refresh of the batch status section."""
    context = await _context(control, None, single=single, single_banner=message, oob=True)
    return TEMPLATES.TemplateResponse(request, "partials/vehicle_result.html", context)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, control: Control) -> HTMLResponse:
    context = await _context(control, None)
    return TEMPLATES.TemplateResponse(request, "dashboard.html", context)


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial(request: Request, control: Control) -> HTMLResponse:
    """Poll target: KPIs while running; once idle, a final KPI + audit-table update (286)."""
    context = await _context(control, None)
    running = bool(context["poll"])
    context["audit_oob"] = not running
    return TEMPLATES.TemplateResponse(
        request, "partials/status_poll.html", context, status_code=200 if running else STOP_POLLING
    )


@router.get("/partials/heartbeat")
async def heartbeat(control: Control, current_last_sync: str | None = None) -> Response:
    """204 (no DOM work) while idle and unchanged; otherwise fire the refresh event."""
    snapshot = await control.snapshot()
    last_sync = snapshot.updated_at.isoformat() if snapshot.updated_at else ""
    if snapshot.status != RUNNING and last_sync == (current_last_sync or ""):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(headers={"HX-Trigger": SYNC_STATE_CHANGED})


@router.get("/partials/kpi", response_class=HTMLResponse)
async def kpi_partial(request: Request, control: Control) -> HTMLResponse:
    """KPI block alone, swapped into #sync-status when the heartbeat reports a change."""
    context = await _context(control, None)
    return TEMPLATES.TemplateResponse(request, "partials/kpi.html", context)


@router.post("/api/sync/trigger", response_class=HTMLResponse)
async def trigger_sync(request: Request, control: Control) -> HTMLResponse:
    started = control.trigger()
    banner = SYNC_STARTED if started else SYNC_ALREADY_RUNNING
    if (await control.config()).dry_run:
        banner += DRY_RUN_NOTE
    return await status_response(request, control, banner, force_poll=started)


@router.post("/api/sync/vehicle", response_class=HTMLResponse)
async def sync_vehicle(
    request: Request,
    control: Control,
    single: Single,
    serial_no: Annotated[str, Form(max_length=128)],
    force_live: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    """Targeted single-vehicle sync; programmatic default is NOT live."""
    serial = serial_no.strip()
    if not serial:
        return await _vehicle_result(request, control, BLANK_SERIAL, None)
    result = await single.sync_single_vehicle(serial, force_live=force_live)
    return await _vehicle_result(request, control, single_sync_banner(result), result)


@router.post("/partials/cursor/reset-now", response_class=HTMLResponse)
async def reset_cursor_now(request: Request, control: Control) -> HTMLResponse:
    return await _apply_cursor(request, control, datetime.now(UTC))


@router.post("/partials/cursor/set", response_class=HTMLResponse)
async def set_cursor(
    request: Request,
    control: Control,
    cursor_timestamp: Annotated[str, Form(max_length=MAX_CURSOR_INPUT)] = "",
) -> HTMLResponse:
    try:
        cursor = parse_cursor(cursor_timestamp, datetime.now(UTC))
    except CursorInputError as exc:
        unprocessable = status.HTTP_422_UNPROCESSABLE_CONTENT
        return await status_response(request, control, str(exc), unprocessable)
    return await _apply_cursor(request, control, cursor)
