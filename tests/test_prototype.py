"""Operacijų centro prototipas: fixture screens, preview levels, env banner, no-op actions."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from src.prototype.views import fixtures, visible_errors

Client = httpx.AsyncClient
SCREENS = [
    "/prototype/",
    "/prototype/procesai/uzduotys",
    "/prototype/paleidimai/p-202",
    "/prototype/irasai/i-1",
    "/prototype/procesai/uzduotys/zurnalas",
    "/prototype/procesai/uzduotys/nustatymai",
]


async def _client(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> AsyncIterator[Client]:
    monkeypatch.setenv("PROTOTYPE_ENABLED", "true" if enabled else "false")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def client(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client]:
    async for http in _client(monkeypatch, enabled=True):
        yield http


def _as(client: Client, level: str, env: str = "DEV") -> Client:
    client.cookies.set("proto_lygis", level)
    client.cookies.set("proto_aplinka", env)
    return client


def test_fixture_covers_every_scenario() -> None:
    data = fixtures()
    modes = {process.id: process.rezimas for process in data.procesai}
    errors = {(r.objektas, r.tipas) for r in data.irasai if r.busena == "klaida"}

    assert modes == {"transporto": "LIVE", "uzduotys": "LIVE", "klientai": "DRY_RUN"}
    assert {("SO029748", "duomenu"), ("SO029761", "sistemos")} <= errors
    assert [r.objektas for r in visible_errors("matyti")] == ["SO029748"]


@pytest.mark.asyncio
async def test_prototype_is_off_by_default(
    isolated_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async for client in _client(monkeypatch, enabled=False):
        assert (await client.get("/prototype/")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SCREENS)
async def test_admin_sees_all_six_screens_in_lithuanian(client: Client, path: str) -> None:
    response = await _as(client, "administruoti").get(path)

    assert response.status_code == 200
    assert '<html lang="lt"' in response.text and "Prototipo vaizdas" in response.text


@pytest.mark.asyncio
async def test_overview_shows_scenarios(client: Client) -> None:
    html = (await _as(client, "valdyti").get("/prototype/")).text

    for text in ("Transporto priemonių sinchronizacija", "Užduočių sinchronizacija"):
        assert text in html
    assert "SO029748" in html and "Nenurodytas technikas" in html and "HTTP 500" in html
    assert "Būtų sinchronizuota: 54" in html


@pytest.mark.asyncio
async def test_basic_level_sees_business_reason_only(client: Client) -> None:
    viewer = _as(client, "matyti")

    overview = (await viewer.get("/prototype/")).text
    record = (await viewer.get("/prototype/irasai/i-1")).text
    hidden = [await viewer.get(path) for path in [*SCREENS[4:], "/prototype/irasai/i-2"]]

    assert "Nenurodytas technikas" in overview and "HTTP 500" not in overview
    assert "Pakartoti" in record and "HTTP 422" not in record and "trace" not in record
    assert [response.status_code for response in hidden] == [403, 403, 403]


@pytest.mark.asyncio
async def test_advanced_record_has_technical_detail(client: Client) -> None:
    html = (await _as(client, "valdyti").get("/prototype/irasai/i-1")).text

    assert "HTTP 422" in html and "assignees" in html and "trace-" in html


@pytest.mark.asyncio
async def test_settings_need_admin_level(client: Client) -> None:
    response = await _as(client, "valdyti").get("/prototype/procesai/uzduotys/nustatymai")

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(("env", "marker"), [("DEV", "proto-env-dev"), ("PROD", "proto-env-prod")])
async def test_environment_banner_variants(client: Client, env: str, marker: str) -> None:
    html = (await _as(client, "administruoti", env).get("/prototype/")).text

    assert marker in html


@pytest.mark.asyncio
async def test_switcher_sets_preview_cookies(client: Client) -> None:
    response = await client.post("/prototype/vaizdas", data={"lygis": "matyti", "aplinka": "PROD"})

    assert response.status_code == 204 and response.headers["HX-Refresh"] == "true"
    assert client.cookies["proto_lygis"] == "matyti" and client.cookies["proto_aplinka"] == "PROD"


@pytest.mark.asyncio
async def test_dry_run_process_page_states_mode(client: Client) -> None:
    html = (await _as(client, "administruoti").get("/prototype/procesai/klientai")).text

    assert "Režimas: DRY RUN" in html and "Būtų sinchronizuota: 54" in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "process_id"), [("live", "klientai"), ("kursorius", "uzduotys")]
)
async def test_dangerous_actions_open_a_dialog_with_required_reason(
    client: Client, action: str, process_id: str
) -> None:
    admin = _as(client, "administruoti")
    page = (await admin.get(f"/prototype/procesai/{process_id}/nustatymai")).text
    form = {"procesas_id": process_id}

    blank = await admin.post(f"/prototype/veiksmai/{action}", data=form | {"priezastis": " "})
    given = await admin.post(f"/prototype/veiksmai/{action}", data=form | {"priezastis": "Testas"})

    assert f'<dialog id="dlg-{action}"' in page and 'name="priezastis"' in page
    assert blank.status_code == 422 and "Nurodykite priežastį" in blank.text
    assert given.status_code == 200 and "niekas nepakeista" in given.text


@pytest.mark.asyncio
async def test_actions_check_the_level_on_the_server(client: Client) -> None:
    form = {"procesas_id": "uzduotys", "priezastis": "Testas"}

    response = await _as(client, "valdyti").post("/prototype/veiksmai/live", data=form)

    assert response.status_code == 403
