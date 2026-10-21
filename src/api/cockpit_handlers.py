"""Cockpit header partial: environment and system-status badges (HTTP transport only)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.cockpit import cockpit_context
from src.api.routes_ui import Control
from src.api.templating import TEMPLATES

cockpit_router = APIRouter()


@cockpit_router.get("/partials/header", response_class=HTMLResponse)
async def header_partial(request: Request, control: Control) -> HTMLResponse:
    """Re-rendered on `sync-state-changed` so the badges follow both streams."""
    context = await cockpit_context(request, await control.snapshot())
    return TEMPLATES.TemplateResponse(request, "partials/header.html", context)


@cockpit_router.get("/partials/diagnostics", response_class=HTMLResponse)
async def diagnostics_partial(request: Request, control: Control) -> HTMLResponse:
    """Diagnostic strip, refreshed on `sync-state-changed` alongside the header."""
    context = await cockpit_context(request, await control.snapshot())
    return TEMPLATES.TemplateResponse(request, "partials/diagnostics.html", context)
