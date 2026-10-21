"""Operacijų centro prototipo fixture modeliai (procesas, paleidimas, įrašas, žurnalas)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Rezimas = Literal["LIVE", "DRY_RUN"]
Lygis = Literal["matyti", "valdyti", "administruoti"]
Aplinka = Literal["DEV", "PROD"]
KlaidosTipas = Literal["duomenu", "sistemos"]
IrasoBusena = Literal["pavyko", "praleista", "klaida"]
ZurnaloLygis = Literal["INFO", "WARN", "ERROR"]
Tema = Literal["dark", "light"]
Kalba = Literal["lt", "en"]


class _Fixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Pranesimas(_Fixture):
    """Verslo klaida ar žurnalo įvykis: kodas + parametrai; tekstas kalbos kataloge (ADR 0002)."""

    kodas: str
    parametrai: dict[str, str] = Field(default_factory=dict)


class Procesas(_Fixture):
    """Pavadinimas ir grafiko aprašas yra kataloge pagal `id`."""

    id: str
    kryptis: str
    saltinis: str
    tikslas: str
    rezimas: Rezimas
    savininkas: str
    kitas_paleidimas: datetime | None
    kursorius: datetime


class Rezultatas(_Fixture):
    apdorota: int
    pavyko: int
    praleista: int
    klaidos: int
    butu_sinchronizuota: int | None = None


class HttpKvietimas(_Fixture):
    sistema: str
    metodas: str
    url: str
    statusas: int
    trukme_s: float


class Paleidimas(_Fixture):
    id: str
    procesas_id: str
    pradzia: datetime
    trukme_s: float
    paleido: str | None  # None = grafikas
    rezimas: Rezimas
    rezultatas: Rezultatas
    watermark: datetime
    http: list[HttpKvietimas]
    trace_id: str


class Techninis(_Fixture):
    http_statusas: int | None
    url: str | None
    atsakymas: str
    taisykle: str | None


class Irasas(_Fixture):
    id: str
    paleidimas_id: str
    procesas_id: str
    objektas: str
    busena: IrasoBusena
    tipas: KlaidosTipas | None
    klaida: Pranesimas | None
    techninis: Techninis | None
    payload: dict[str, JsonValue] | None
    trace_id: str


class ZurnaloIrasas(_Fixture):
    laikas: datetime
    lygis: ZurnaloLygis
    procesas_id: str
    ivykis: Pranesimas
    kas: str | None  # None = grafikas
    trace_id: str


class Fixtures(_Fixture):
    procesai: list[Procesas]
    paleidimai: list[Paleidimas]
    irasai: list[Irasas]
    zurnalas: list[ZurnaloIrasas]
