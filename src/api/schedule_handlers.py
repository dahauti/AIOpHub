"""Dashboard schedule controls: incremental cadence and weekly full sync (HTTP transport only)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from src.api.routes_ui import Control
from src.api.templating import TEMPLATES
from src.schemas.schedule import ScheduleSettings
from src.schemas.sync import RuntimeConfig

INVALID_SCHEDULE = "Invalid schedule"

schedule_router = APIRouter()


def schedule_card(
    request: Request, config: RuntimeConfig, banner: str | None, status_code: int = 200
) -> HTMLResponse:
    context = {"config": config, "schedule_banner": banner}
    return TEMPLATES.TemplateResponse(
        request, "partials/schedule_card.html", context, status_code=status_code
    )


def validation_banner(exc: ValidationError) -> str:
    """First validation problem, named by its form field."""
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error["loc"])
    return f"{INVALID_SCHEDULE}: {field}: {error['msg']}"


@schedule_router.post("/partials/config/schedule", response_class=HTMLResponse)
async def update_schedule(
    request: Request,
    control: Control,
    schedule_mode: Annotated[str, Form(max_length=16)],
    nightly_time: Annotated[str, Form(max_length=8)],
    interval_minutes: Annotated[str, Form(max_length=8)],
    full_sync_weekday: Annotated[str, Form(max_length=2)],
    full_sync_time: Annotated[str, Form(max_length=8)],
    full_sync_enabled: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    """Validate and store the whole schedule; an unchecked switch is simply absent (off)."""
    try:
        schedule = ScheduleSettings.model_validate(
            {
                "schedule_mode": schedule_mode,
                "nightly_time": nightly_time,
                "interval_minutes": interval_minutes,
                "full_sync_enabled": full_sync_enabled,
                "full_sync_weekday": full_sync_weekday,
                "full_sync_time": full_sync_time,
            }
        )
    except ValidationError as exc:
        unprocessable = status.HTTP_422_UNPROCESSABLE_CONTENT
        return schedule_card(request, await control.config(), validation_banner(exc), unprocessable)
    config = await control.update_schedule(schedule)
    return schedule_card(request, config, f"Schedule saved: {config.describe()}")
