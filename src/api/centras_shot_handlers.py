"""Operations Center "Testinis šūvis": one BC order through the EXISTING task canary (HTTP only).

Same gate as every /centras screen: 404 unless CENTRAS_ENABLED and a request from this
machine. Later this action requires the Administruoti level (permissions module). The owner
picks DRY RUN or LIVE per shot and confirms in a dialog; the server enforces the mode through
`trigger_single(force_dry=…, force_live=…)`. No sync logic lives here.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.api.centras_handlers import SHOT, _page, _view, require_owner
from src.api.cockpit import task_control
from src.config import get_settings
from src.domain.canary import MAX_TARGET_CHARS, CanaryTarget, is_guid
from src.domain.mapping import FALLBACK_STATUS
from src.domain.task_builders import TASKER_PATCH_FIELDS
from src.services.line_reconciler import GUID_FIELD as LINE_GUID_FIELD
from src.services.task_outbound import OBJECT_TYPE as ORDER_OBJECT_TYPE

Mode = Literal["DRY_RUN", "LIVE"]
# What a LIVE canary writes to the BC header (task_outbound: tasker_patch with FALLBACK_STATUS).
HEADER_WRITES = ", ".join(
    f"{name} = {FALLBACK_STATUS.bc_status}" if name == "DocumentStatus" else name
    for name in TASKER_PATCH_FIELDS
)

centras_shot_router = APIRouter(prefix=SHOT, dependencies=[Depends(require_owner)])


def _form(
    request: Request,
    order: str = "",
    mode: Mode = "DRY_RUN",
    *,
    confirm: bool = False,
    notice: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    settings = get_settings()
    context: dict[str, object] = {
        "uzsakymas": order,
        "rezimas": mode,
        "patvirtinti": confirm,
        "pranesimas": notice,
        "frontu": httpx.URL(settings.active_frontu_base_url).host,
        "bc": settings.active_bc_company,
        "antraste": HEADER_WRITES,
        "eilutes": LINE_GUID_FIELD,
    }
    response = _page(request, "testinis.html", context)
    response.status_code = status_code
    return response


def _order_error(order: str) -> str | None:
    """Catalog key of why the input is not a BC order No, else None."""
    if not order:
        return "centras.testinis.klaida_tuscias"
    if len(order) > MAX_TARGET_CHARS:
        return "centras.testinis.klaida_ilgas"
    if is_guid(order):
        return "centras.testinis.klaida_guid"
    return None


@centras_shot_router.get("", response_class=HTMLResponse)
async def shot_form(
    request: Request, uzsakymas: Annotated[str, Query(max_length=MAX_TARGET_CHARS)] = ""
) -> HTMLResponse:
    """Empty form, or pre-filled from a record page ("Kartoti"); always DRY RUN by default."""
    return _form(request, uzsakymas.strip())


@centras_shot_router.post("", response_class=HTMLResponse)
async def shot_confirm(
    request: Request,
    uzsakymas: Annotated[str, Form()] = "",
    rezimas: Annotated[Mode, Form()] = "DRY_RUN",
) -> HTMLResponse:
    """Step 1: validate and show the confirmation dialog; nothing starts yet."""
    order = uzsakymas.strip()
    error = _order_error(order)
    if error is not None:
        return _form(request, order, rezimas, notice=error, status_code=422)
    return _form(request, order, rezimas, confirm=True)


@centras_shot_router.post("/paleisti", response_model=None)
async def shot_run(
    request: Request,
    uzsakymas: Annotated[str, Form()] = "",
    rezimas: Annotated[Mode, Form()] = "DRY_RUN",
) -> Response:
    """Step 2 (confirmed): start the existing canary, then wait for its record."""
    order = uzsakymas.strip()
    error = _order_error(order)
    if error is not None:
        return _form(request, order, rezimas, notice=error, status_code=422)
    control = task_control(request)
    if control is None:
        notice = "centras.testinis.neprijungta"
        return _form(request, order, rezimas, notice=notice, status_code=409)
    since = int(datetime.now(UTC).timestamp())
    target = CanaryTarget(kind="order", value=order)
    live = rezimas == "LIVE"
    if not control.trigger_single(target, force_live=live, force_dry=not live):
        notice = "centras.testinis.uzimta"
        return _form(request, order, rezimas, notice=notice, status_code=409)
    query = urlencode({"uzsakymas": order, "nuo": since})
    return RedirectResponse(f"{SHOT}/laukia?{query}", status.HTTP_303_SEE_OTHER)


@centras_shot_router.get("/laukia", response_model=None)
async def shot_wait(
    request: Request,
    uzsakymas: Annotated[str, Query(max_length=MAX_TARGET_CHARS)],
    nuo: Annotated[int, Query(ge=0)],
) -> Response:
    """Redirect to the record once this shot's canary run has finished; else wait."""
    since = datetime.fromtimestamp(nuo, UTC)
    outcome = await _view().canary_outcome(ORDER_OBJECT_TYPE, uzsakymas, since)
    if outcome is not None:
        return RedirectResponse(f"/centras/irasai/{outcome.outcome_id}", status.HTTP_303_SEE_OTHER)
    control = task_control(request)
    running = control is not None and (await control.cockpit()).running
    context: dict[str, object] = {"uzsakymas": uzsakymas, "vyksta": running}
    return _page(request, "testinis_laukia.html", context)
