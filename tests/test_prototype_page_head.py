"""Prototipo puslapio antraštės panelė (UX_GUIDELINES 2 sk.): faktai iš fixture, be dublių."""

import html as html_lib
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.main import create_app

Client = httpx.AsyncClient
PROTO_CSS = (Path(__file__).resolve().parents[1] / "src" / "static" / "proto.css").read_text(
    encoding="utf-8"
)
FACT = re.compile(
    r'<div class="proto-fact"><dt class="proto-fact-label">(.*?)</dt>'
    r'<dd class="proto-fact-value">(.*?)</dd></div>',
    re.S,
)
# page -> expected (label, value) facts for the administruoti level, LT; values are substrings
PAGES: dict[str, list[tuple[str, str]]] = {
    "/prototype/": [("Procesų", "3"), ("LIVE procesų", "2")],
    "/prototype/procesai/uzduotys": [
        ("Režimas", "LIVE"),
        (
            "Grafikas",
            "Kas 15 min. (atviras klausimas: periodinis ar įvykiais)"
            " Kitas paleidimas: 2026-10-21 15:00",
        ),
        (
            "Paskutinis paleidimas",
            "2026-10-21 14:45 Pavyko: 21 · praleista 0 · klaidų 2 · apdorota 23",
        ),
        ("Atviros klaidos", "2"),
    ],
    "/prototype/paleidimai/p-202": [
        ("Pradžia", "2026-10-21 14:45"),
        ("Režimas", "LIVE"),
        ("Rezultatas", "Pavyko: 21 · praleista 0 · klaidų 2 · apdorota 23"),
        ("Trukmė", "3,4 s"),
        ("Paleido", "Grafikas"),
    ],
    "/prototype/irasai/i-1": [
        ("Būsena", "Klaida"),
        ("Procesas", "Užduočių sinchronizacija"),
        ("Aptikta", "2026-10-21 14:45"),
        ("Kas taiso", "Skyrius, BC'e"),
    ],
    "/prototype/procesai/uzduotys/zurnalas": [
        ("Procesas", "Užduočių sinchronizacija"),
        ("Įvykių", "5 įvykiai"),
        ("Paskutinis įvykis", "2026-10-21 14:45"),
    ],
    "/prototype/procesai/uzduotys/nustatymai": [
        ("Režimas", "LIVE"),
        ("Kursorius", "2026-10-21 14:30"),
        ("Grafikas", "Kas 15 min. (atviras klausimas: periodinis ar įvykiais)"),
    ],
}


@pytest_asyncio.fixture
async def client(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client]:
    monkeypatch.setenv("PROTOTYPE_ENABLED", "true")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _text(fragment: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def facts(page: str) -> list[tuple[str, str]]:
    return [(_text(label), _text(value)) for label, value in FACT.findall(page)]


async def _get(client: Client, path: str, level: str = "administruoti", lang: str = "lt") -> str:
    client.cookies.set("proto_lygis", level)
    client.cookies.set("proto_kalba", lang)
    return (await client.get(path)).text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_every_page_starts_with_one_panel_and_its_facts(client: Client, path: str) -> None:
    page = await _get(client, path)
    found = facts(page)

    assert page.count('<header class="proto-page-head"') == 1
    assert 2 <= len(found) <= 5
    assert [label for label, _ in found] == [label for label, _ in PAGES[path]]
    for (label, value), (_, expected) in zip(found, PAGES[path], strict=True):
        assert expected in value, label


@pytest.mark.asyncio
async def test_matyti_sees_only_the_basic_run_facts(client: Client) -> None:
    page = await _get(client, "/prototype/paleidimai/p-202", level="matyti")

    assert [label for label, _ in facts(page)] == ["Pradžia", "Režimas", "Rezultatas"]


@pytest.mark.asyncio
async def test_error_page_has_a_panel_without_facts(client: Client) -> None:
    page = await _get(client, "/prototype/procesai/uzduotys/zurnalas", level="matyti")

    assert '<header class="proto-page-head" role="alert">' in page and facts(page) == []


@pytest.mark.asyncio
async def test_successful_record_shows_status_once_and_no_fixer(client: Client) -> None:
    page = await _get(client, "/prototype/irasai/i-3")
    title = page[page.index('<h1 class="proto-title">') : page.index("</h1>")]

    assert ("Būsena", "Pavyko") in facts(page) and ("Kas taiso", "—") in facts(page)
    assert "pill-ok" not in title


@pytest.mark.asyncio
async def test_facts_follow_the_language(client: Client) -> None:
    page = await _get(client, "/prototype/irasai/i-1", lang="en")

    assert ("Status", "Error") in facts(page) and ("Who fixes", "Department, in BC") in facts(page)


@pytest.mark.asyncio
async def test_facts_shown_in_the_panel_are_not_repeated_below(client: Client) -> None:
    process = await _get(client, "/prototype/procesai/uzduotys")
    run = await _get(client, "/prototype/paleidimai/p-202")
    settings = await _get(client, "/prototype/procesai/uzduotys/nustatymai")

    for card in ("Būsena", "Grafikas", "Atviros klaidos"):
        assert f'<p class="label">{card}</p>' not in process
    assert "proto-num" not in run and "<dt>Trukmė</dt>" not in run and "<dt>Paleido</dt>" not in run
    assert settings.count("2026-10-21 14:30") == 1 and "rašo</p>" not in settings


def test_layout_uses_the_shared_spacing_scale_and_grid() -> None:
    steps = dict(re.findall(r"--proto-space-(\d): ([0-9.]+)rem", PROTO_CSS))

    assert {int(k): float(v) for k, v in steps.items()} == {i: i * 0.5 for i in range(1, 6)}
    assert "grid-template-columns: repeat(var(--proto-cols), minmax(0, 1fr))" in PROTO_CSS
    for rule in (".proto-fact", ".proto-card", ".proto-section"):
        body = re.search(rf"^{re.escape(rule)} \{{([^}}]*)\}}", PROTO_CSS, re.M)
        assert body and "var(--proto-space-2)" in body.group(1), rule
