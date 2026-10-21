"""Glass Box partials: pipeline schema, diagnostic event viewer and canary steps (HTTP only)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.cockpit import cockpit_context, debug_events, task_control
from src.api.routes_ui import STOP_POLLING, Control
from src.api.templating import TEMPLATES

glass_box_router = APIRouter()


@glass_box_router.get("/partials/pipeline", response_class=HTMLResponse)
async def pipeline_partial(request: Request, control: Control) -> HTMLResponse:
    """3-node schema, refreshed on `sync-state-changed` alongside the diagnostic strip."""
    context = await cockpit_context(request, await control.snapshot())
    return TEMPLATES.TemplateResponse(request, "partials/pipeline_schema.html", context)


@glass_box_router.get("/partials/debug-console", response_class=HTMLResponse)
async def debug_console_partial(request: Request) -> HTMLResponse:
    context = {"debug_events": await debug_events(request)}
    return TEMPLATES.TemplateResponse(request, "partials/debug_console.html", context)


@glass_box_router.get("/partials/canary/steps", response_class=HTMLResponse)
async def canary_steps_partial(request: Request) -> HTMLResponse:
    """Polled while the canary runs; 286 swaps the final steps and stops the timer."""
    control = task_control(request)
    canary = (await control.cockpit()).canary if control is not None else None
    running = canary is not None and canary.state == "RUNNING"
    return TEMPLATES.TemplateResponse(
        request,
        "partials/canary_steps.html",
        {"canary": canary},
        status_code=200 if running else STOP_POLLING,
    )
