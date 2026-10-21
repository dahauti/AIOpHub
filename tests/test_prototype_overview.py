"""Prototipo Apžvalga (kompaktiška): be kelio, eilutės pagal klaidos tipą, procesų lentelė."""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from src.prototype.views import overview_errors, processes_by_attention

Client = httpx.AsyncClient
PROTO_CSS = (Path(__file__).resolve().parents[1] / "src" / "static" / "proto.css").read_text(
    encoding="utf-8"
)
DEEPER = [
    "/prototype/procesai/uzduotys",
    "/prototype/paleidimai/p-202",
    "/prototype/irasai/i-1",
    "/prototype/procesai/uzduotys/zurnalas",
    "/prototype/procesai/uzduotys/nustatymai",
]
HEADERS = [
    "Pavadinimas",
    "Režimas",
    "Būsena",
    "Paskutinis paleidimas",
    "Rezultatas",
    "Kitas paleidimas",
]


@pytest_asyncio.fixture
async def client(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client]:
    monkeypatch.setenv("PROTOTYPE_ENABLED", "true")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _overview(client: Client, level: str) -> str:
    client.cookies.set("proto_lygis", level)
    return (await client.get("/prototype/")).text


def _section(page: str, heading_id: str) -> str:
    start = page.index(f'id="{heading_id}"')
    return page[start : page.index("</section>", start)]


def _rule(selector: str) -> str:
    body = re.search(rf"^{re.escape(selector)} \{{([^}}]*)\}}", PROTO_CSS, re.M)
    assert body is not None, selector
    return body.group(1)


def test_system_errors_come_before_data_errors() -> None:
    assert [i.tipas for i in overview_errors("valdyti")] == ["sistemos", "duomenu"]
    assert [i.tipas for i in overview_errors("matyti")] == ["duomenu"]


def test_processes_needing_action_come_first() -> None:
    for level in ("matyti", "administruoti"):
        order = [p.id for p in processes_by_attention(level)]
        assert order == ["uzduotys", "transporto", "klientai"], level


@pytest.mark.asyncio
async def test_overview_has_no_breadcrumb_but_deeper_pages_do(client: Client) -> None:
    assert 'class="proto-crumbs"' not in await _overview(client, "administruoti")
    for path in DEEPER:
        assert 'class="proto-crumbs"' in (await client.get(path)).text, path


@pytest.mark.asyncio
async def test_action_rows_are_plain_rows_in_type_order(client: Client) -> None:
    rows = _section(await _overview(client, "administruoti"), "veiksmo-h")

    objects = re.findall(r'<span class="mono-id proto-row-name">([^<]+)</span>', rows)
    assert objects == ["SO029761", "SO029748"]
    assert "proto-card" not in rows


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["matyti", "valdyti", "administruoti"])
async def test_process_table_one_linked_row_per_process(client: Client, level: str) -> None:
    table = _section(await _overview(client, level), "procesai-h")
    headers = re.findall(r"<th>([^<]+)</th>", table)
    rows = re.findall(r'<tr class="proto-link-row">(.*?)</tr>', table, re.S)

    assert headers == HEADERS
    assert len(rows) == 3 and "proto-card" not in table
    for row in rows:
        assert len(re.findall(r"<a ", row)) == 1 and 'class="proto-row-name proto-stretched"' in row
    assert "/prototype/procesai/uzduotys" in rows[0]


@pytest.mark.asyncio
async def test_matyti_sees_no_system_errors_in_needs_action(client: Client) -> None:
    rows = _section(await _overview(client, "matyti"), "veiksmo-h")

    assert "SO029748" in rows and "SO029761" not in rows


def test_only_the_item_name_is_underlined_and_rows_have_no_frame() -> None:
    row = _rule(".proto-row")

    assert "text-decoration: none" in row
    assert "background-color" not in row and "border-radius" not in row and "border:" not in row
    assert "border-top" in _rule(".proto-row + .proto-row")
    assert "text-decoration: underline" in _rule(".proto-row-name")
    assert "position: absolute; inset: 0" in _rule(".proto-stretched::after")
    assert "position: relative" in _rule(".proto-link-row")
