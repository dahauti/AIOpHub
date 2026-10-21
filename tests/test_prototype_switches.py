"""Prototipo perjungikliai (UX_GUIDELINES 3 sk.): segmentai, meniu, fokusas, cache-busting."""

import hashlib
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from src.prototype import assets
from src.prototype.i18n import KALBOS

Client = httpx.AsyncClient
ROOT = Path(__file__).resolve().parents[1] / "src"
LAYOUT = (ROOT / "templates" / "prototype" / "_layout.html").read_text(encoding="utf-8")
MACROS = (ROOT / "templates" / "prototype" / "_macros.html").read_text(encoding="utf-8")
PROTO_CSS = (ROOT / "static" / "proto.css").read_text(encoding="utf-8")
RADIO = re.compile(
    r'class="proto-(?:seg|menu)-input" name="([a-z]+)" value="([A-Za-z]+)"( checked)?'
)
ASSETS = ("app.css", "hub.css", "proto.css", "proto.js")
# group (= form field) -> (cookie, every value)
GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "tema": ("proto_tema", ("light", "dark")),
    "kalba": ("proto_kalba", ("lt", "en")),
    "lygis": ("proto_lygis", ("matyti", "valdyti", "administruoti")),
    "aplinka": ("proto_aplinka", ("DEV", "PROD")),
}
DEFAULTS = {"tema": "dark", "kalba": "lt", "lygis": "administruoti", "aplinka": "DEV"}
ONE_GROUP_CASES = [(group, value) for group, (_, values) in GROUPS.items() for value in values]


@pytest_asyncio.fixture
async def client(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client]:
    monkeypatch.setenv("PROTOTYPE_ENABLED", "true")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def checked_by_group(html: str) -> dict[str, list[str]]:
    """Group -> values rendered with `checked` (every group present, even if none is checked)."""
    found: dict[str, list[str]] = {}
    for name, value, checked in RADIO.findall(html):
        found.setdefault(name, [])
        if checked:
            found[name].append(value)
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize(("group", "value"), ONE_GROUP_CASES)
async def test_exactly_one_option_per_group_is_checked_and_matches_cookie(
    client: Client, group: str, value: str
) -> None:
    client.cookies.set(GROUPS[group][0], value)

    checked = checked_by_group((await client.get("/prototype/")).text)

    assert checked == {g: [value if g == group else DEFAULTS[g]] for g in GROUPS}


@pytest.mark.asyncio
async def test_invalid_cookies_check_the_defaults(client: Client) -> None:
    for cookie, _ in GROUPS.values():
        client.cookies.set(cookie, "nonsense")

    checked = checked_by_group((await client.get("/prototype/")).text)

    assert checked == {g: [v] for g, v in DEFAULTS.items()}


@pytest.mark.asyncio
async def test_every_focus_fragment_has_its_target(client: Client) -> None:
    html = (await client.get("/prototype/")).text
    fragments = set(re.findall(r'data-focus="([a-z-]+)"', html))

    assert fragments == {"seg-lygis", "seg-aplinka", "user-menu"}
    for fragment in ("seg-lygis", "seg-aplinka"):
        group = html[html.index(f'<fieldset id="{fragment}"') :]
        assert group[: group.index("</fieldset>")].count(" checked") == 1, fragment
    menu = html[html.index('<details class="proto-user" id="user-menu">') :]
    assert '<summary class="proto-user-trigger"' in menu[: menu.index("</details>")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kalba", "label"),
    [
        ("lt", "Vartotojo meniu: Tomas Griškevičius, kalba LT"),
        ("en", "User menu: Tomas Griškevičius, language EN"),
    ],
)
async def test_user_menu_trigger_shows_initials_and_language(
    client: Client, kalba: str, label: str
) -> None:
    client.cookies.set("proto_kalba", kalba)

    html = (await client.get("/prototype/")).text

    assert f'aria-label="{label}">TG &middot; {kalba.upper()} &#9662;</summary>' in html


@pytest.mark.asyncio
async def test_user_menu_lists_every_language_and_a_disabled_sign_out(client: Client) -> None:
    html = (await client.get("/prototype/")).text
    menu = html[html.index('<details class="proto-user"') : html.index("</details>")]

    assert len(re.findall(r'class="proto-menu-input" name="kalba"', menu)) == len(KALBOS)
    assert len(re.findall(r'class="proto-menu-input" name="tema"', menu)) == 2
    assert '<p class="proto-user-name">Tomas Griškevičius</p>' in menu
    assert re.search(r'class="proto-user-signout" disabled title="[^"]+">Atsijungti<', menu)
    assert "☀" not in menu and "☾" not in menu


@pytest.mark.asyncio
async def test_page_references_the_current_content_hash_of_every_asset(client: Client) -> None:
    html = (await client.get("/prototype/")).text

    for name in ASSETS:
        digest = hashlib.sha256((ROOT / "static" / name).read_bytes()).hexdigest()[:12]
        assert f"/static/{name}?v={digest}" in html, name
    assert (await client.get(f"/static/proto.js?v={digest}")).status_code == 200


def test_asset_hash_follows_file_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assets, "STATIC_DIR", tmp_path)
    css = tmp_path / "x.css"
    css.write_text("a {}", encoding="utf-8")
    before = assets.asset_url("x.css")
    css.write_text("b {}", encoding="utf-8")
    stat = css.stat()
    os.utime(css, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert before != assets.asset_url("x.css")
    assert assets.asset_url("x.css").endswith(hashlib.sha256(b"b {}").hexdigest()[:12])


@pytest.mark.asyncio
async def test_group_labels_are_visible_and_come_from_the_catalog(client: Client) -> None:
    lt = (await client.get("/prototype/")).text
    client.cookies.set("proto_kalba", "en")
    en = (await client.get("/prototype/")).text

    for legend in ("Tema", "Kalba", "Vaizdas", "Aplinka"):
        assert f">{legend}</legend>" in lt
    for legend in ("Theme", "Language", "Preview", "Environment"):
        assert f">{legend}</legend>" in en


def test_prototype_layout_has_no_native_select() -> None:
    assert "<select" not in LAYOUT and "<select" not in MACROS


def test_menu_current_option_is_check_mark_and_bold() -> None:
    checked = r"\.proto-menu-input:checked \+ \.proto-menu-text"
    current = re.search(checked + r" \{([^}]*)\}", PROTO_CSS)
    mark = re.search(checked + r"::before \{([^}]*)\}", PROTO_CSS)

    assert current and "font-weight: 700" in current.group(1)
    assert mark and '"\\2713' in mark.group(1)


def test_selected_state_differs_by_fill_weight_and_check_mark() -> None:
    checked = r"\.proto-seg-input:checked \+ \.proto-seg-text"
    selected = re.search(checked + r" \{([^}]*)\}", PROTO_CSS)
    mark = re.search(checked + r"::before \{([^}]*)\}", PROTO_CSS)
    hover = re.search(r"\.proto-seg-option:hover [^{]*\{([^}]*)\}", PROTO_CSS)

    assert selected and "background-color" in selected.group(1)
    assert "font-weight: 700" in selected.group(1)
    assert mark and '"\\2713' in mark.group(1)
    assert hover and "font-weight" not in hover.group(1) and "content" not in hover.group(1)
    assert ":not(:checked)" in hover.group(0)
