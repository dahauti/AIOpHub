"""HTMX dashboard routes: HTTP transport only, backed by the SyncControl port."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.pipelines.scheduler import SyncControl
from src.pipelines.single_vehicle import SingleVehicleControl
from src.schemas.sync import SingleSyncResult

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
SYNC_STARTED = "Sync started"
SYNC_ALREADY_RUNNING = "Sync already in progress"
DRY_RUN_NOTE = " (dry run: no changes will be sent to Frontu)"
SINGLE_BUSY = "Sync already in progress. Please wait for the current batch to complete."
BLANK_SERIAL = "Enter a vehicle SerialNo"


def format_utc(value: datetime | None) -> str:
    """Render timestamps uniformly in UTC; em dash when absent."""
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_iso_utc(value: datetime) -> str:
    """Machine-readable UTC instant for `<time datetime>`; the browser localises it."""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


TEMPLATES.env.filters["utc"] = format_utc
TEMPLATES.env.filters["iso_utc"] = format_iso_utc


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


async def _status(
    request: Request,
    control: SyncControl,
    banner: str | None,
    single: SingleSyncResult | None = None,
) -> HTMLResponse:
    """Status partial; a single-vehicle result also carries the out-of-band debug panel."""
    context = {
        "snapshot": await control.snapshot(),
        "banner": banner,
        "dry_run": control.dry_run,
        "single": single,
    }
    template = "partials/status.html" if single is None else "partials/vehicle_result.html"
    return TEMPLATES.TemplateResponse(request, template, context)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, control: Control) -> HTMLResponse:
    context = {"snapshot": await control.snapshot(), "banner": None, "dry_run": control.dry_run}
    return TEMPLATES.TemplateResponse(request, "dashboard.html", context)


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial(request: Request, control: Control) -> HTMLResponse:
    return await _status(request, control, banner=None)


@router.post("/api/sync/trigger", response_class=HTMLResponse)
async def trigger_sync(request: Request, control: Control) -> HTMLResponse:
    banner = SYNC_STARTED if control.trigger() else SYNC_ALREADY_RUNNING
    if control.dry_run:
        banner += DRY_RUN_NOTE
    return await _status(request, control, banner=banner)


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
        return await _status(request, control, banner=BLANK_SERIAL)
    result = await single.sync_single_vehicle(serial, force_live=force_live)
    return await _status(request, control, single_sync_banner(result), result)
