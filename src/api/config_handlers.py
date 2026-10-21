"""Dashboard runtime controls: dry-run and auto-sync toggles, run abort (HTTP transport only)."""

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse

from src.api.routes_ui import Control, status_response
from src.pipelines.scheduler import SyncControl
from src.schemas.sync import ConfigFlag, RuntimeConfig

MODE_LOCKED = "Sync is running: the mode cannot be changed until it finishes"
SYNC_ABORTED = "Sync aborted"
NOTHING_TO_ABORT = "No sync is running"

config_router = APIRouter()


def toggle_banner(flag: ConfigFlag, config: RuntimeConfig) -> str:
    """User-facing confirmation of the flag's new value."""
    if flag == "dry_run":
        if config.dry_run:
            return "Mode set to DRY RUN: no changes will be sent to Frontu"
        return "Mode set to LIVE: the next sync writes to Frontu"
    return f"Auto-sync {'enabled' if config.auto_sync_enabled else 'disabled'}"


async def _toggle(request: Request, control: SyncControl, flag: ConfigFlag) -> HTMLResponse:
    config = await control.toggle(flag)
    if config is None:
        return await status_response(request, control, MODE_LOCKED, status.HTTP_409_CONFLICT)
    return await status_response(request, control, toggle_banner(flag, config))


@config_router.post("/partials/config/toggle-dry-run", response_class=HTMLResponse)
async def toggle_dry_run(request: Request, control: Control) -> HTMLResponse:
    return await _toggle(request, control, "dry_run")


@config_router.post("/partials/config/toggle-auto-sync", response_class=HTMLResponse)
async def toggle_auto_sync(request: Request, control: Control) -> HTMLResponse:
    return await _toggle(request, control, "auto_sync_enabled")


@config_router.post("/partials/sync/abort", response_class=HTMLResponse)
async def abort_sync(request: Request, control: Control) -> HTMLResponse:
    if await control.abort():
        return await status_response(request, control, SYNC_ABORTED)
    return await status_response(request, control, NOTHING_TO_ABORT, status.HTTP_409_CONFLICT)
