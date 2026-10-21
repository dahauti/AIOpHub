"""Prototipo kalbos (ADR 0002): pranešimų katalogai, CLDR daugiskaita, datos ir skaičiai.

Katalogas yra plokščias JSON: raktas → tekstas arba daugiskaitos formos {one, few, other}.
Tekste `{vardas}` pakeičiamas parametru. `_fmt.*` raktai aprašo kalbos formatus.
"""

import json
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, get_args

from pydantic import TypeAdapter

from src.prototype.models import Kalba

PluralForm = Literal["zero", "one", "two", "few", "many", "other"]
Catalog = dict[str, str | dict[PluralForm, str]]

CATALOG_DIR = Path(__file__).resolve().parent / "catalogs"
KALBOS: tuple[Kalba, ...] = get_args(Kalba)
_catalog: TypeAdapter[Catalog] = TypeAdapter(Catalog)


def plural_lt(n: int) -> PluralForm:
    """CLDR lt sveikiesiems: `many` skirta tik trupmenoms, todėl čia negrąžinama."""
    n = abs(n)
    teens = 11 <= n % 100 <= 19
    if n % 10 == 1 and not teens:
        return "one"
    if 2 <= n % 10 <= 9 and not teens:
        return "few"
    return "other"


def plural_en(n: int) -> PluralForm:
    return "one" if n == 1 else "other"


PLURAL_RULES: dict[Kalba, Callable[[int], PluralForm]] = {"lt": plural_lt, "en": plural_en}


@lru_cache(maxsize=len(KALBOS))
def catalog(kalba: Kalba) -> Catalog:
    path = CATALOG_DIR / f"{kalba}.json"
    return _catalog.validate_python(json.loads(path.read_text(encoding="utf-8")))


class Vertejas:
    """Vienos kalbos tekstai ir formatai; šablonams perduodami `t`, `tn`, `laikas`, `sk`."""

    def __init__(self, kalba: Kalba) -> None:
        self.kalba = kalba
        self._catalog = catalog(kalba)

    def _text(self, key: str) -> str:
        value = self._catalog[key]
        if not isinstance(value, str):
            raise TypeError(f"Catalog key {key!r} has plural forms; use tn()")
        return value

    def t(self, key: str, **params: object) -> str:
        return self._text(key).format_map(params)

    def tn(self, key: str, n: int, **params: object) -> str:
        forms = self._catalog[key]
        if isinstance(forms, str):
            raise TypeError(f"Catalog key {key!r} has no plural forms; use t()")
        form = PLURAL_RULES[self.kalba](n)
        return forms.get(form, forms["other"]).format_map(params | {"n": self.sk(n, 0)})

    def sk(self, value: float, decimals: int = 1) -> str:
        """Skaičius su kalbos dešimtainiu ženklu ir tūkstančių skyrikliu."""
        text = f"{value:,.{decimals}f}"
        group, decimal = self._text("_fmt.group"), self._text("_fmt.decimal")
        return text.replace(",", "\0").replace(".", decimal).replace("\0", group)

    def laikas(self, value: datetime) -> str:
        months = self._text("_fmt.months").split()
        return self._text("_fmt.datetime").format(
            Y=value.year,
            m=f"{value.month:02d}",
            d=f"{value.day:02d}",
            j=value.day,
            mon=months[value.month - 1],
            H=f"{value.hour:02d}",
            M=f"{value.minute:02d}",
        )
