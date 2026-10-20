"""Precompiled stylesheet: no runtime Tailwind JIT, and every template class has a rule."""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl

ROOT = Path(__file__).resolve().parents[1] / "src"
CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
SINGLE_WORD_CLASSES = {"flex", "grid", "border", "truncate", "card", "label", "field", "badge"}
NOT_STYLING = {"dark"}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.state.sync_control = FakeControl(SNAPSHOT)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _template_classes() -> set[str]:
    """Static and Jinja-literal class tokens used in any template."""
    tokens: set[str] = set()
    for path in (ROOT / "templates").rglob("*.html"):
        for value in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            for token in value.replace("{{", " ").replace("}}", " ").replace("'", " ").split():
                styling = "-" in token or token in SINGLE_WORD_CLASSES
                if styling and re.fullmatch(r"[a-z0-9:\[\]#./-]+", token):
                    tokens.add(token)
    return tokens - NOT_STYLING


def _selector(token: str) -> str:
    return "." + re.sub(r"([:\[\]#./])", r"\\\1", token)


def test_every_template_class_has_a_css_rule() -> None:
    classes = _template_classes()
    missing = sorted(t for t in classes if _selector(t) not in CSS and _selector(t) not in BASE)

    assert len(classes) > 60
    assert missing == []


@pytest.mark.asyncio
async def test_page_uses_static_stylesheet_without_runtime_css_compiler(
    client: httpx.AsyncClient,
) -> None:
    page = (await client.get("/")).text
    css = await client.get("/static/app.css")

    assert '<link rel="stylesheet" href="/static/app.css">' in page
    for runtime in ("cdn.tailwindcss.com", "tailwind.config", "text/tailwindcss"):
        assert runtime not in page
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert ".card {" in css.text and ".min-h-\\[96px\\]" in css.text
