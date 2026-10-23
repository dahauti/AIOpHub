"""Operations Center, first real screens (HTTP only): read-only views of job_runs/record_outcomes.

Owner-only until the permissions module exists: every route answers 404 unless CENTRAS_ENABLED
and the request comes from this machine. Nothing here writes data; the one POST sets the
viewer's theme/language cookie. Look and texts are the prototype's (ADR 0002).
"""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import TypeAdapter, ValidationError

from src.api.prototype_templates import TEMPLATES
from src.config import get_settings
from src.database import get_engine
from src.prototype.i18n import Vertejas, catalog
from src.prototype.models import Aplinka, Kalba, Tema, Vartotojas
from src.repositories.job_run_repository import JobRunRepository
from src.repositories.operations_read_repository import OperationsReadRepository
from src.services.error_catalog import message
from src.services.operations_view import (
    RUNS_PER_PROCESS,
    OperationsView,
    duration_seconds,
    local,
)
from src.services.task_outbound import OBJECT_TYPE as ORDER_OBJECT_TYPE
from src.services.task_runner import LOCK_MODULE as TASKS_PROCESS

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1"})
SHOT = "/centras/testinis"  # Testinis šūvis form (centras_shot_handlers)
THEME_COOKIE, LANG_COOKIE = "centras_tema", "centras_kalba"
COOKIE_PATH = "/centras"
# CSS class per stored code (proto.css / hub.css); the text comes from the catalog.
CLASSES: dict[str, dict[str, str]] = {
    "busena": {
        "running": "pill-running",
        "succeeded": "pill-ok",
        "failed": "pill-failed",
        "aborted": "pill-idle",
        "skipped": "pill-idle",
    },
    "rezultatas": {"ok": "pill-ok", "skipped": "pill-idle", "error": "pill-failed"},
    "rezimas": {"LIVE": "proto-badge-live", "DRY_RUN": "proto-badge-dry"},
    "tipas": {"data": "proto-type-data", "system": "proto-type-system"},
}
_themes: TypeAdapter[Tema] = TypeAdapter(Tema)
_langs: TypeAdapter[Kalba] = TypeAdapter(Kalba)


def require_owner(request: Request) -> None:
    client = request.client
    local_call = client is not None and client.host in LOCAL_HOSTS
    if not (get_settings().centras_enabled and local_call):
        raise HTTPException(status.HTTP_404_NOT_FOUND)


centras_router = APIRouter(prefix="/centras", dependencies=[Depends(require_owner)])


def _view() -> OperationsView:
    engine = get_engine()
    return OperationsView(OperationsReadRepository(engine), JobRunRepository(engine))


def _preference[T](request: Request, name: str, adapter: TypeAdapter[T], default: T) -> T:
    try:
        return adapter.validate_python(request.cookies.get(name, default))
    except ValidationError:
        return default


def _page(request: Request, template: str, context: dict[str, object]) -> HTMLResponse:
    theme = _preference(request, THEME_COOKIE, _themes, "dark")
    lang = _preference(request, LANG_COOKIE, _langs, "lt")
    v = Vertejas(lang)
    env: Aplinka = "PROD" if get_settings().app_env == "prod" else "DEV"

    def process_name(process_id: str) -> str:
        key = f"centras.procesas.{process_id}"
        return v.t(key) if key in catalog(lang) else process_id

    def error_text(
        code: str, params: dict[str, str], kalba: Kalba = lang, http_status: int | None = None
    ) -> str:
        """Catalog sentence; the stored HTTP status fills `{http_status}` when the code has it."""
        status_param = {"http_status": str(http_status)} if http_status is not None else {}
        return message(code, kalba, status_param | params)

    base: dict[str, object] = {
        "tema": theme,
        "kalba": lang,
        "aplinka": env,
        "vartotojas": Vartotojas(vardas=v.t("centras.vartotojas")),
        "t": v.t,
        "tn": v.tn,
        "sk": v.sk,
        "laikas": lambda value: v.laikas(local(value)),
        "trukme": duration_seconds,
        "pavadinimas": process_name,
        "klaida": error_text,
        "klases": CLASSES,
        "nuostatos_url": f"{COOKIE_PATH}/nuostatos",
    }
    return TEMPLATES.TemplateResponse(request, f"centras/{template}", base | context)


def _found[T](item: T | None) -> T:
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return item


@centras_router.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    return _page(request, "apzvalga.html", {"apzvalga": await _view().overview()})


@centras_router.get("/procesai/{process_id}", response_class=HTMLResponse)
async def process_page(request: Request, process_id: str) -> HTMLResponse:
    item = _found(await _view().process_runs(process_id))
    shot = SHOT if process_id == TASKS_PROCESS else None  # the test shot runs the task canary
    context = {"procesas": item, "riba": RUNS_PER_PROCESS, "testinis_url": shot}
    return _page(request, "procesas.html", context)


@centras_router.get("/paleidimai/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, run_id: str) -> HTMLResponse:
    item = _found(await _view().run_detail(run_id))
    return _page(request, "paleidimas.html", {"paleidimas": item})


@centras_router.get("/irasai/{outcome_id}", response_class=HTMLResponse)
async def record_page(request: Request, outcome_id: str) -> HTMLResponse:
    item = _found(await _view().record(outcome_id))
    outcome = item.outcome
    repeat = None  # only a BC order can be shot again; the form opens pre-filled, not started
    if outcome.object_type == ORDER_OBJECT_TYPE:
        repeat = f"{SHOT}?{urlencode({'uzsakymas': outcome.object_id})}"
    return _page(request, "irasas.html", {"irasas": item, "kartoti_url": repeat})


@centras_router.post("/nuostatos")
async def switch_preferences(
    tema: Annotated[Tema, Form()], kalba: Annotated[Kalba, Form()]
) -> Response:
    """Viewer's theme and language cookie only (path /centras); no data is written."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Refresh": "true"})
    for key, value in ((THEME_COOKIE, tema), (LANG_COOKIE, kalba)):
        response.set_cookie(key, value, httponly=True, samesite="lax", path=COOKIE_PATH)
    return response
