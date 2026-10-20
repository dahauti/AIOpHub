"""HTMX dashboard routes: HTTP transport only, backed by the SyncControl port."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.pipelines.scheduler import SyncControl

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
SYNC_STARTED = "Sync started"
SYNC_ALREADY_RUNNING = "Sync already in progress"


def format_utc(value: datetime | None) -> str:
    """Render timestamps uniformly in UTC; em dash when absent."""
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


TEMPLATES.env.filters["utc"] = format_utc


def get_sync_control(request: Request) -> SyncControl:
    control = request.app.state.sync_control
    if not isinstance(control, SyncControl):
        raise RuntimeError("Sync control is not initialised; is the app lifespan running?")
    return control


Control = Annotated[SyncControl, Depends(get_sync_control)]
router = APIRouter()


async def _status(request: Request, control: SyncControl, banner: str | None) -> HTMLResponse:
    context = {"snapshot": await control.snapshot(), "banner": banner}
    return TEMPLATES.TemplateResponse(request, "partials/status.html", context)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, control: Control) -> HTMLResponse:
    context = {"snapshot": await control.snapshot(), "banner": None}
    return TEMPLATES.TemplateResponse(request, "dashboard.html", context)


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial(request: Request, control: Control) -> HTMLResponse:
    return await _status(request, control, banner=None)


@router.post("/api/sync/trigger", response_class=HTMLResponse)
async def trigger_sync(request: Request, control: Control) -> HTMLResponse:
    banner = SYNC_STARTED if control.trigger() else SYNC_ALREADY_RUNNING
    return await _status(request, control, banner=banner)
