"""Prototipo tema ir kalbos (ADR 0002): katalogai, daugiskaita, formatai, slapukai, šablonai."""

import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import get_args

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from src.prototype import models
from src.prototype.i18n import KALBOS, PLURAL_RULES, Vertejas, catalog, plural_en, plural_lt
from src.prototype.views import VEIKSMAI, fixtures

Client = httpx.AsyncClient
TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "templates" / "prototype"
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
COMMENT = re.compile(r"\{#.*?#\}", re.S)
LITERAL = re.compile(r"\"([^\"]*)\"|'([^']*)'")
USER_ATTR = re.compile(r'(?:title|aria-label|placeholder|alt)="([^"]*)"')
LETTER = re.compile(r"[^\W\d_]")
NBSP = chr(0xA0)  # LT tūkstančių skyriklis
TECHNICAL = {"id", "equalto", "%Y-%m-%dT%H:%M"}
ENUMS = {
    value
    for alias in (
        models.Rezimas,
        models.Lygis,
        models.Aplinka,
        models.KlaidosTipas,
        models.IrasoBusena,
        models.ZurnaloLygis,
        models.Tema,
        models.Kalba,
    )
    for value in get_args(alias)
} | set(VEIKSMAI)


@pytest_asyncio.fixture
async def client(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client]:
    monkeypatch.setenv("PROTOTYPE_ENABLED", "true")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _literal_ok(literal: str) -> bool:
    """Template string literals may only be codes, catalog key fragments or machine values."""
    core = literal.strip(".")
    return (
        not LETTER.search(literal)
        or literal.endswith(".html")
        or literal.startswith("proto-")
        or literal in ENUMS | TECHNICAL
        or any(f".{core}." in f".{key}." for key in catalog("lt"))
    )


def hardcoded_text(source: str) -> list[str]:
    """User-facing text written directly in a template instead of coming from the catalog."""
    source = COMMENT.sub("", source)
    found = [v for v in USER_ATTR.findall(source) if LETTER.search(JINJA.sub("", v))]
    for block in JINJA.findall(source):
        found += [a or b for a, b in LITERAL.findall(block) if not _literal_ok(a or b)]
    text = re.sub(r"&[#a-z0-9]+;", " ", re.sub(r"<[^>]*>", " ", JINJA.sub(" ", source)))
    return found + [word for word in text.split() if LETTER.search(word)]


def test_every_key_exists_in_every_catalog() -> None:
    lt = catalog("lt")

    for kalba in KALBOS:
        assert catalog(kalba).keys() == lt.keys(), kalba
        for key, value in catalog(kalba).items():
            assert isinstance(value, str) == isinstance(lt[key], str), (kalba, key)


@pytest.mark.parametrize("kalba", KALBOS)
def test_plural_entries_have_exactly_the_forms_the_rule_returns(kalba: models.Kalba) -> None:
    forms = {PLURAL_RULES[kalba](n) for n in range(1000)}

    for key, value in catalog(kalba).items():
        if not isinstance(value, str):
            assert set(value) == forms, (kalba, key)


@pytest.mark.parametrize(
    ("n", "form"),
    [
        (0, "other"),
        (1, "one"),
        (2, "few"),
        (5, "few"),
        (10, "other"),
        (11, "other"),
        (12, "other"),
        (20, "other"),
        (21, "one"),
        (22, "few"),
        (111, "other"),
    ],
)
def test_lithuanian_plural_forms(n: int, form: str) -> None:
    assert plural_lt(n) == form


def test_lithuanian_integers_never_use_many() -> None:
    assert {plural_lt(n) for n in range(10_000)} == {"one", "few", "other"}


@pytest.mark.parametrize(("n", "form"), [(0, "other"), (1, "one"), (2, "other"), (21, "other")])
def test_english_plural_forms(n: int, form: str) -> None:
    assert plural_en(n) == form


def test_plural_text_uses_the_right_form() -> None:
    lt = Vertejas("lt")

    assert [lt.tn("zurnalas.kiek", n) for n in (1, 3, 11)] == [
        "Rodomas 1 įvykis.",
        "Rodomi 3 įvykiai.",
        "Rodoma 11 įvykių.",
    ]


def test_numbers_and_dates_follow_the_language() -> None:
    moment = datetime(2026, 10, 21, 14, 5)
    lt, en = Vertejas("lt"), Vertejas("en")

    assert (lt.sk(1234.5), en.sk(1234.5)) == (f"1{NBSP}234,5", "1,234.5")
    assert (lt.laikas(moment), en.laikas(moment)) == ("2026-10-21 14:05", "21 Oct 2026 14:05")


@pytest.mark.parametrize("kalba", KALBOS)
def test_every_fixture_code_has_text_in_every_language(kalba: models.Kalba) -> None:
    t, data = Vertejas(kalba).t, fixtures()

    for item in data.irasai:
        if item.klaida:
            for part in ("priezastis", "ka_daryti"):
                assert t(f"klaida.{item.klaida.kodas}.{part}", **item.klaida.parametrai)
    for entry in data.zurnalas:
        assert t(f"ivykis.{entry.ivykis.kodas}", **entry.ivykis.parametrai)
    for process in data.procesai:
        assert t(f"procesas.{process.id}.pavadinimas") and t(f"procesas.{process.id}.grafikas")


def test_text_check_catches_hardcoded_text() -> None:
    sample = '<p title="Pagalba">Labas {{ t("app.pavadinimas") }}</p>{{ "Frontu" if x }}'

    assert hardcoded_text(sample) == ["Pagalba", "Frontu", "Labas"]


@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.html")), ids=lambda p: p.name)
def test_no_hardcoded_user_facing_text_in_prototype_templates(path: Path) -> None:
    assert hardcoded_text(path.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_defaults_are_dark_and_lithuanian(client: Client) -> None:
    html = (await client.get("/prototype/")).text

    assert '<html lang="lt" data-theme="dark">' in html


@pytest.mark.asyncio
async def test_english_light_page_is_rendered_by_the_server(client: Client) -> None:
    client.cookies.set("proto_tema", "light")
    client.cookies.set("proto_kalba", "en")

    record = (await client.get("/prototype/irasai/i-1")).text
    overview = (await client.get("/prototype/")).text

    assert '<html lang="en" data-theme="light">' in record
    assert "No technician assigned" in record and "BC order SO029748" in record
    assert "Nenurodytas" not in record
    assert "Needs action: 2 records" in overview and "21 Oct 2026" in overview


@pytest.mark.asyncio
async def test_invalid_preference_cookies_fall_back_to_defaults(client: Client) -> None:
    client.cookies.set("proto_tema", "neon")
    client.cookies.set("proto_kalba", "xx")

    html = (await client.get("/prototype/")).text

    assert '<html lang="lt" data-theme="dark">' in html


@pytest.mark.asyncio
async def test_switcher_sets_preference_cookies(client: Client) -> None:
    response = await client.post("/prototype/nuostatos", data={"tema": "light", "kalba": "en"})

    assert response.status_code == 204 and response.headers["HX-Refresh"] == "true"
    assert client.cookies["proto_tema"] == "light" and client.cookies["proto_kalba"] == "en"


@pytest.mark.asyncio
async def test_action_messages_follow_the_language(client: Client) -> None:
    client.cookies.set("proto_kalba", "en")
    form = {"procesas_id": "klientai", "priezastis": " "}

    response = await client.post("/prototype/veiksmai/live", data=form)

    assert response.status_code == 422 and "Enter a reason." in response.text
