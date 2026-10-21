"""Prototipo skaitymo logika: fixture užkrovimas, lygių taisyklės ir veiksmų katalogas.

Lygiai (koncepcija „Vartotojai ir teisių lygiai“): Matyti mato tik duomenų klaidas ir verslo
priežastį; Valdyti prideda techninę detalę, žurnalą ir paleidimą; Administruoti prideda
nustatymus.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from src.prototype.models import Fixtures, Irasas, Lygis, Paleidimas, Procesas, ZurnaloIrasas

FIXTURE_FILE = Path(__file__).resolve().parent / "fixtures.json"
RANGAS: dict[Lygis, int] = {"matyti": 0, "valdyti": 1, "administruoti": 2}


class Veiksmas(BaseModel):
    """Prototipo veiksmas: nieko nekeičia, tik parodo dialogą ir serverio patikras."""

    model_config = ConfigDict(frozen=True)

    pavadinimas: str
    lygis: Lygis
    pavojingas: bool
    reikia_priezasties: bool
    aprasas: str = ""
    grizta: str = ""


VEIKSMAI: dict[str, Veiksmas] = {
    "pakartoti": Veiksmas(
        pavadinimas="Pakartoti", lygis="matyti", pavojingas=False, reikia_priezasties=False
    ),
    "paleisti": Veiksmas(
        pavadinimas="Paleisti",
        lygis="valdyti",
        pavojingas=True,
        reikia_priezasties=False,
        aprasas="Paleidžia procesą dabar ir rašo į tikslo sistemą pagal proceso režimą.",
        grizta="Negrįžtama: įrašyti pakeitimai lieka tikslo sistemoje.",
    ),
    "testinis": Veiksmas(
        pavadinimas="Testinis šūvis", lygis="valdyti", pavojingas=False, reikia_priezasties=False
    ),
    "dryrun": Veiksmas(
        pavadinimas="Perjungti į DRY RUN",
        lygis="administruoti",
        pavojingas=False,
        reikia_priezasties=False,
    ),
    "live": Veiksmas(
        pavadinimas="Įjungti LIVE",
        lygis="administruoti",
        pavojingas=True,
        reikia_priezasties=True,
        aprasas="Procesas pradės rašyti į tikslo sistemą kiekvieno paleidimo metu.",
        grizta="Grįžtama: režimą galima vėl perjungti į DRY RUN.",
    ),
    "kursorius": Veiksmas(
        pavadinimas="Keisti kursorių",
        lygis="administruoti",
        pavojingas=True,
        reikia_priezasties=True,
        aprasas="Kitas paleidimas apdoros visus įrašus, pakeistus po naujo kursoriaus.",
        grizta="Iš dalies grįžtama: jau apdoroti įrašai lieka apdoroti.",
    ),
}


@lru_cache(maxsize=1)
def fixtures() -> Fixtures:
    return Fixtures.model_validate(json.loads(FIXTURE_FILE.read_text(encoding="utf-8")))


def can(level: Lygis, needed: Lygis) -> bool:
    return RANGAS[level] >= RANGAS[needed]


def process(process_id: str) -> Procesas:
    return next(p for p in fixtures().procesai if p.id == process_id)


def run(run_id: str) -> Paleidimas:
    return next(r for r in fixtures().paleidimai if r.id == run_id)


def record(record_id: str) -> Irasas:
    return next(r for r in fixtures().irasai if r.id == record_id)


def runs_of(process_id: str) -> list[Paleidimas]:
    runs = [r for r in fixtures().paleidimai if r.procesas_id == process_id]
    return sorted(runs, key=lambda r: r.pradzia, reverse=True)


def last_run(process_id: str) -> Paleidimas | None:
    runs = runs_of(process_id)
    return runs[0] if runs else None


def record_visible(level: Lygis, item: Irasas) -> bool:
    """Matyti lygis sistemos klaidų nemato (koncepcija „Klaidų modelis“)."""
    return item.tipas != "sistemos" or can(level, "valdyti")


def visible_errors(level: Lygis, process_id: str | None = None) -> list[Irasas]:
    return [
        item
        for item in fixtures().irasai
        if item.busena == "klaida"
        and record_visible(level, item)
        and (process_id is None or item.procesas_id == process_id)
    ]


def run_records(level: Lygis, run_id: str) -> list[Irasas]:
    return [r for r in fixtures().irasai if r.paleidimas_id == run_id and record_visible(level, r)]


def journal(process_id: str) -> list[ZurnaloIrasas]:
    entries = [e for e in fixtures().zurnalas if e.procesas_id == process_id]
    return sorted(entries, key=lambda e: e.laikas, reverse=True)
