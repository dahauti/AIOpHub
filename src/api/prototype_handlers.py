"""Operacijų centro UX prototipas (HTTP only): fixture ekranai ir veiksmai be poveikio.

Mounted always, but every route answers 404 unless PROTOTYPE_ENABLED. There is no auth in
this app yet: the preview level comes from a cookie set by the prototype-only switcher, and
the server still checks it for every screen and action, as the real permissions module will.
Theme and language (ADR 0002) also come from cookies, so the server renders them without a flash.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import TypeAdapter, ValidationError

from src.api.prototype_templates import TEMPLATES
from src.config import get_settings
from src.prototype import views
from src.prototype.i18n import Vertejas
from src.prototype.models import Aplinka, Kalba, Lygis, Tema

LEVEL_COOKIE, ENV_COOKIE = "proto_lygis", "proto_aplinka"
THEME_COOKIE, LANG_COOKIE = "proto_tema", "proto_kalba"
DEFAULT_LEVEL: Lygis = "administruoti"
DEFAULT_ENV: Aplinka = "DEV"
DEFAULT_THEME: Tema = "dark"
DEFAULT_LANG: Kalba = "lt"
MAX_REASON = 500


def require_enabled() -> None:
    if not get_settings().prototype_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)


prototype_router = APIRouter(prefix="/prototype", dependencies=[Depends(require_enabled)])


_levels: TypeAdapter[Lygis] = TypeAdapter(Lygis)
_envs: TypeAdapter[Aplinka] = TypeAdapter(Aplinka)
_themes: TypeAdapter[Tema] = TypeAdapter(Tema)
_langs: TypeAdapter[Kalba] = TypeAdapter(Kalba)


def _cookie[T](request: Request, name: str, adapter: TypeAdapter[T], default: T) -> T:
    """Cookie value validated against its Literal type; missing or invalid -> default."""
    try:
        return adapter.validate_python(request.cookies.get(name, default))
    except ValidationError:
        return default


def preview(request: Request) -> tuple[Lygis, Aplinka]:
    """Prototype-only preview level and environment; invalid cookies fall back to defaults."""
    level = _cookie(request, LEVEL_COOKIE, _levels, DEFAULT_LEVEL)
    return level, _cookie(request, ENV_COOKIE, _envs, DEFAULT_ENV)


def preferences(request: Request) -> tuple[Tema, Kalba]:
    """Theme and language; invalid cookies fall back to dark + LT."""
    theme = _cookie(request, THEME_COOKIE, _themes, DEFAULT_THEME)
    return theme, _cookie(request, LANG_COOKIE, _langs, DEFAULT_LANG)


def _find[T](finder: Callable[[str], T], key: str) -> T:
    try:
        return finder(key)
    except StopIteration:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from None


def _page(
    request: Request, template: str, context: dict[str, object], status_code: int = 200
) -> HTMLResponse:
    level, env = preview(request)
    theme, lang = preferences(request)
    v = Vertejas(lang)
    base: dict[str, object] = {
        "lygis": level,
        "aplinka": env,
        "tema": theme,
        "kalba": lang,
        "procesai": views.fixtures().procesai,
        "vartotojas": views.fixtures().vartotojas,
        "t": v.t,
        "tn": v.tn,
        "laikas": v.laikas,
        "sk": v.sk,
        "nuostatos_url": "/prototype/nuostatos",
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
    context: dict[str, object] = {
        "irasas": item,
        "procesas": views.process(item.procesas_id),
        "paleidimas": views.run(item.paleidimas_id),
    }
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


@prototype_router.post("/nuostatos")
async def switch_preferences(
    tema: Annotated[Tema, Form()], kalba: Annotated[Kalba, Form()]
) -> Response:
    """Theme and language cookie (prototype path only; the real hub will use "/")."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Refresh": "true"})
    for key, value in ((THEME_COOKIE, tema), (LANG_COOKIE, kalba)):
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
    level, v = preview(request)[0], Vertejas(preferences(request)[1])
    context: dict[str, object] = {"raktas": action, "procesas": item, "ok": False}
    if not views.can(level, veiksmas.lygis):
        lygis = v.t(f"lygis.{veiksmas.lygis}")
        message, code = v.t("atsakymas.reikia_lygio", lygis=lygis), status.HTTP_403_FORBIDDEN
    elif veiksmas.reikia_priezasties and not priezastis.strip():
        message = v.t("atsakymas.nurodykite_priezasti")
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        message, code = v.t("atsakymas.neatlikta"), status.HTTP_200_OK
        context |= {"ok": True, "priezastis": priezastis.strip()}
    context["pranesimas"] = message
    return _page(request, "_atsakymas.html", context, code)
