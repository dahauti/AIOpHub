"""Operacijų centro UX prototipas (HTTP only): fixture ekranai ir veiksmai be poveikio.

Mounted always, but every route answers 404 unless PROTOTYPE_ENABLED. There is no auth in
this app yet: the preview level comes from a cookie set by the prototype-only switcher, and
the server still checks it for every screen and action, as the real permissions module will.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import TypeAdapter, ValidationError

from src.api.templating import http_reason
from src.config import get_settings
from src.prototype import views
from src.prototype.models import Aplinka, Lygis

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
TEMPLATES.env.globals["laikas"] = lambda value: value.strftime("%Y-%m-%d %H:%M")
TEMPLATES.env.globals["sk"] = lambda value: f"{value:.1f}".replace(".", ",")
TEMPLATES.env.filters["http_reason"] = http_reason
for helper in ("can", "last_run", "runs_of", "visible_errors", "run_records", "record_visible"):
    TEMPLATES.env.globals[helper] = getattr(views, helper)
TEMPLATES.env.globals["veiksmai"] = views.VEIKSMAI

LEVEL_COOKIE, ENV_COOKIE = "proto_lygis", "proto_aplinka"
DEFAULT_LEVEL: Lygis = "administruoti"
DEFAULT_ENV: Aplinka = "DEV"
MAX_REASON = 500
_levels: TypeAdapter[Lygis] = TypeAdapter(Lygis)
_envs: TypeAdapter[Aplinka] = TypeAdapter(Aplinka)


def require_enabled() -> None:
    if not get_settings().prototype_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)


prototype_router = APIRouter(prefix="/prototype", dependencies=[Depends(require_enabled)])


def preview(request: Request) -> tuple[Lygis, Aplinka]:
    """Prototype-only preview level and environment; invalid cookies fall back to defaults."""
    try:
        level = _levels.validate_python(request.cookies.get(LEVEL_COOKIE, DEFAULT_LEVEL))
    except ValidationError:
        level = DEFAULT_LEVEL
    try:
        env = _envs.validate_python(request.cookies.get(ENV_COOKIE, DEFAULT_ENV))
    except ValidationError:
        env = DEFAULT_ENV
    return level, env


def _find[T](finder: Callable[[str], T], key: str) -> T:
    try:
        return finder(key)
    except StopIteration:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from None


def _page(
    request: Request, template: str, context: dict[str, object], status_code: int = 200
) -> HTMLResponse:
    level, env = preview(request)
    base: dict[str, object] = {
        "lygis": level,
        "aplinka": env,
        "procesai": views.fixtures().procesai,
    }
    return TEMPLATES.TemplateResponse(
        request, f"prototype/{template}", base | context, status_code=status_code
    )


def _guarded(
    request: Request, needed: Lygis, template: str, context: dict[str, object]
) -> HTMLResponse:
    if not views.can(preview(request)[0], needed):
        forbidden = status.HTTP_403_FORBIDDEN
        return _page(request, "draudziama.html", {"reikia": needed}, forbidden)
    return _page(request, template, context)


@prototype_router.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    return _page(request, "apzvalga.html", {})


@prototype_router.get("/procesai/{process_id}", response_class=HTMLResponse)
async def process_page(request: Request, process_id: str) -> HTMLResponse:
    return _page(request, "procesas.html", {"procesas": _find(views.process, process_id)})


@prototype_router.get("/paleidimai/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, run_id: str) -> HTMLResponse:
    run = _find(views.run, run_id)
    context: dict[str, object] = {"paleidimas": run, "procesas": views.process(run.procesas_id)}
    return _page(request, "paleidimas.html", context)


@prototype_router.get("/irasai/{record_id}", response_class=HTMLResponse)
async def record_page(request: Request, record_id: str) -> HTMLResponse:
    item = _find(views.record, record_id)
    needed: Lygis = "matyti" if views.record_visible("matyti", item) else "valdyti"
    context: dict[str, object] = {"irasas": item, "procesas": views.process(item.procesas_id)}
    return _guarded(request, needed, "irasas.html", context)


@prototype_router.get("/procesai/{process_id}/zurnalas", response_class=HTMLResponse)
async def journal_page(request: Request, process_id: str) -> HTMLResponse:
    item = _find(views.process, process_id)
    context: dict[str, object] = {"procesas": item, "zurnalas": views.journal(process_id)}
    return _guarded(request, "valdyti", "zurnalas.html", context)


@prototype_router.get("/procesai/{process_id}/nustatymai", response_class=HTMLResponse)
async def settings_page(request: Request, process_id: str) -> HTMLResponse:
    item = _find(views.process, process_id)
    return _guarded(request, "administruoti", "nustatymai.html", {"procesas": item})


@prototype_router.post("/vaizdas")
async def switch_preview(
    lygis: Annotated[Lygis, Form()], aplinka: Annotated[Aplinka, Form()]
) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Refresh": "true"})
    for key, value in ((LEVEL_COOKIE, lygis), (ENV_COOKIE, aplinka)):
        response.set_cookie(key, value, httponly=True, samesite="lax", path="/prototype")
    return response


@prototype_router.post("/veiksmai/{action}", response_class=HTMLResponse)
async def run_action(
    request: Request,
    action: str,
    procesas_id: Annotated[str, Form(max_length=64)],
    priezastis: Annotated[str, Form(max_length=MAX_REASON)] = "",
) -> HTMLResponse:
    """No effect: validates level and reason exactly as the real action would, then says so."""
    veiksmas = views.VEIKSMAI.get(action)
    if veiksmas is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    item = _find(views.process, procesas_id)
    level = preview(request)[0]
    context: dict[str, object] = {"veiksmas": veiksmas, "procesas": item, "ok": False}
    if not views.can(level, veiksmas.lygis):
        message, code = f"Šiam veiksmui reikia lygio „{veiksmas.lygis}“.", 403
    elif veiksmas.reikia_priezasties and not priezastis.strip():
        message, code = "Nurodykite priežastį.", status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        message, code = "Prototipas: veiksmas neatliktas, niekas nepakeista.", 200
        context |= {"ok": True, "priezastis": priezastis.strip()}
    context["pranesimas"] = message
    return _page(request, "_atsakymas.html", context, code)
