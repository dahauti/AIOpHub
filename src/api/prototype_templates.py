"""Prototipo Jinja aplinka: view helperiai, pasirinkimų sąrašai ir statinių failų URL."""

from pathlib import Path
from typing import get_args

from fastapi.templating import Jinja2Templates

from src.api.templating import http_reason
from src.prototype import views
from src.prototype.assets import asset_url
from src.prototype.i18n import KALBOS
from src.prototype.models import Aplinka, Lygis

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
TEMPLATES.env.filters["http_reason"] = http_reason
HELPERS = (
    "can",
    "last_run",
    "runs_of",
    "visible_errors",
    "run_records",
    "record_visible",
    "overview_errors",
    "processes_by_attention",
)
for helper in HELPERS:
    TEMPLATES.env.globals[helper] = getattr(views, helper)
TEMPLATES.env.globals |= {
    "veiksmai": views.VEIKSMAI,
    "lygiai": get_args(Lygis),
    "aplinkos": get_args(Aplinka),
    "temos": ("light", "dark"),  # rodymo tvarka: Šviesi | Tamsi
    "kalbos": KALBOS,
    "asset": asset_url,
}
