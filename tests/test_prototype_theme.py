"""Prototipo temos (ADR 0002, alwark.lt paletė): WCAG kontrastas ir self-hosted šriftai."""

import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.main import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "static"
LAYOUT = STATIC.parent / "templates" / "prototype" / "_layout.html"
FONT_CDNS = ("fonts.googleapis.com", "fonts.gstatic.com")
WCAG_AA = 4.5
# (foreground, background) token pairs used for body text in the prototype
TEXT_PAIRS = [
    ("text", "bg"),
    ("text", "surface"),
    ("text", "field"),
    ("text-strong", "surface"),
    ("text-soft", "surface"),
    ("text-muted", "bg"),
    ("text-muted", "surface"),
    ("text-faint", "bg"),
    ("text-faint", "surface"),
    ("text-faint", "sunken"),
    ("link", "bg"),
    ("link", "surface"),
    ("accent-text", "bg"),
    ("accent-text", "surface"),
    ("ok-text", "surface"),
    ("warn-text", "surface"),
    ("err-text", "surface"),
]


def theme_tokens(theme: str) -> dict[str, str]:
    """Hex --hub-* values of one hub.css theme block, 3-digit hex expanded."""
    css = (STATIC / "hub.css").read_text(encoding="utf-8")
    block = re.search(rf':root\[data-theme="{theme}"\] \{{(.*?)\}}', css, re.S)
    assert block is not None, theme
    tokens = dict(re.findall(r"--hub-([a-z0-9-]+):\s*#([0-9a-fA-F]{3,6})\b", block.group(1)))
    expand = {k: "".join(c * 2 for c in v) if len(v) == 3 else v for k, v in tokens.items()}
    return {k: v.lower() for k, v in expand.items()}


def _luminance(hex_value: str) -> float:
    channels = [int(hex_value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    high, low = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_contrast_formula_matches_wcag_reference() -> None:
    assert round(contrast("000000", "ffffff"), 1) == 21.0
    assert round(contrast("777777", "ffffff"), 2) == 4.48


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS)
def test_main_text_pairs_meet_wcag_aa(theme: str, fg: str, bg: str) -> None:
    tokens = theme_tokens(theme)

    assert contrast(tokens[fg], tokens[bg]) >= WCAG_AA, (theme, fg, bg)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fill", ["crimson", "crimson-2"])
def test_white_text_on_accent_fills_meets_wcag_aa(theme: str, fill: str) -> None:
    assert contrast("ffffff", theme_tokens(theme)[fill]) >= WCAG_AA


def test_prototype_amber_on_prod_bar_meets_wcag_aa() -> None:
    proto = (STATIC / "proto.css").read_text(encoding="utf-8")
    amber = re.search(r"\.proto-top-prod \{ --proto-amber: #([0-9a-f]{6});", proto)

    assert amber is not None
    for theme in ("dark", "light"):
        assert contrast(amber.group(1), theme_tokens(theme)["crimson"]) >= WCAG_AA, theme


def test_prototype_layout_loads_no_font_cdn() -> None:
    layout = LAYOUT.read_text(encoding="utf-8")

    assert not [cdn for cdn in FONT_CDNS if cdn in layout]


def test_self_hosted_fonts_exist_and_match_recorded_sha256() -> None:
    urls = re.findall(r'url\("/static/(fonts/[^"]+)"\)', (STATIC / "proto.css").read_text("utf-8"))
    readme = (STATIC / "fonts" / "README.md").read_text(encoding="utf-8")

    assert len(urls) == 2
    for path in [*urls, "fonts/OFL.txt"]:
        digest = hashlib.sha256((STATIC / path).read_bytes()).hexdigest()
        assert f"`{Path(path).name}`" in readme and digest in readme, path


@pytest_asyncio.fixture
async def client(
    isolated_env: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("PROTOTYPE_ENABLED", "true")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.mark.asyncio
async def test_rendered_prototype_page_has_no_font_cdn(client: httpx.AsyncClient) -> None:
    html = (await client.get("/prototype/")).text
    font = await client.get("/static/fonts/open-sans-latin-ext-wght-normal.woff2")

    assert not [cdn for cdn in FONT_CDNS if cdn in html]
    assert font.status_code == 200


@pytest.mark.asyncio
async def test_header_shows_logo_linking_home(client: httpx.AsyncClient) -> None:
    html = (await client.get("/prototype/")).text
    brand = re.search(r'<a href="/prototype/" class="proto-brand">(.*?)</a>', html, re.S)
    assert brand is not None
    img = re.search(r'<img class="proto-logo" src="(/static/brand/logo\.png\?v=\w+)"', brand[1])
    assert img is not None
    assert 'alt="Alwark"' in brand[1] and 'width="102" height="24"' in brand[1]
    assert (await client.get(img[1])).status_code == 200


def test_logo_turns_white_on_dark_theme_and_prod_bar() -> None:
    css = (STATIC / "proto.css").read_text(encoding="utf-8")
    white = re.search(r"^(.*)\{ filter: brightness\(0\) invert\(1\); \}", css, re.M)
    assert white is not None
    selectors = {s.strip() for s in white[1].split(",")}
    assert selectors == {':root[data-theme="dark"] .proto-logo', ".proto-top-prod .proto-logo"}


def _proto_var(selector: str, name: str) -> str:
    """Hex value of one custom property inside a proto.css rule that starts with `selector`."""
    css = (STATIC / "proto.css").read_text(encoding="utf-8")
    rules = re.findall(rf"^{re.escape(selector)} \{{([^}}]*)\}}", css, re.M)
    found = [m for r in rules for m in re.findall(rf"--{name}: #([0-9a-f]{{3,6}});", r)]
    assert len(found) == 1, (selector, name, found)
    return "".join(c * 2 for c in found[0]) if len(found[0]) == 3 else found[0]


def segment_pairs(theme: str) -> list[tuple[str, str, str, float]]:
    """(case, foreground, background, minimum) for segmented controls on every bar."""
    tok = theme_tokens(theme)
    ink_selector = ':root[data-theme="light"] .proto-body' if theme == "light" else ".proto-body"
    ink = _proto_var(ink_selector, "proto-amber-ink")
    prod_amber = _proto_var(".proto-top-prod", "proto-amber")
    prod_ink = _proto_var(".proto-top-prod", "proto-amber-ink")
    white, crimson = "ffffff", tok["crimson"]
    return [
        ("header unselected", tok["text-soft"], tok["surface"], WCAG_AA),
        ("header accent selected", white, tok["crimson-2"], WCAG_AA),
        ("header amber selected", ink, tok["warn-text"], WCAG_AA),
        ("header focus ring", tok["link"], tok["surface"], 3.0),
        ("dev bar unselected", tok["text-soft"], tok["raised"], WCAG_AA),
        ("dev bar amber selected", ink, tok["warn-text"], WCAG_AA),
        ("dev bar amber outline", tok["warn-text"], tok["raised"], 3.0),
        ("dev bar focus ring", tok["link"], tok["raised"], 3.0),
        ("prod bar unselected", white, crimson, WCAG_AA),
        ("prod bar amber selected", prod_ink, prod_amber, WCAG_AA),
        ("prod bar focus ring", white, crimson, 3.0),
        ("prod header title", white, crimson, WCAG_AA),
        ("prod header env badge", crimson, white, WCAG_AA),
        ("prod header PROTOTIPAS badge", prod_amber, crimson, WCAG_AA),
        ("prod header menu trigger", white, crimson, WCAG_AA),
        ("dev header env badge", tok["text"], tok["raised"], WCAG_AA),
        ("dev header title", tok["text-strong"], tok["surface"], WCAG_AA),
        ("dev header PROTOTIPAS badge", tok["warn-text"], tok["surface"], WCAG_AA),
        ("menu trigger", tok["text"], tok["surface"], WCAG_AA),
        ("menu option", tok["text-soft"], tok["surface"], WCAG_AA),
        ("menu current option", tok["text-strong"], tok["surface"], WCAG_AA),
        ("menu group label", tok["text-muted"], tok["surface"], WCAG_AA),
        ("menu focus ring", tok["link"], tok["surface"], 3.0),
    ]


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_segmented_controls_meet_wcag(theme: str) -> None:
    failing = [
        (case, round(contrast(fg, bg), 2))
        for case, fg, bg, minimum in segment_pairs(theme)
        if contrast(fg, bg) < minimum
    ]

    assert failing == []
