"""Every Jinja filter the dashboard templates use is registered on the shared environment.

Route tests only render the templates they hit; this check parses all of them, so a template
calling an unregistered filter fails here instead of as a TemplateRuntimeError at runtime.
"""

from pathlib import Path

import httpx
import pytest
from jinja2 import nodes

from src.api.routes_ui import TEMPLATES
from src.main import create_app
from tests.ui_fakes import SNAPSHOT, FakeControl

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "src" / "templates"


def _used_filters() -> set[str]:
    used: set[str] = set()
    for path in TEMPLATE_DIR.rglob("*.html"):
        ast = TEMPLATES.env.parse(path.read_text(encoding="utf-8"))
        used |= {node.name for node in ast.find_all(nodes.Filter)}
    return used


def test_every_filter_used_by_templates_is_registered() -> None:
    used = _used_filters()

    assert {"utc", "iso_utc"} <= used
    assert used - set(TEMPLATES.env.filters) == set()


@pytest.mark.asyncio
async def test_status_partial_renders_time_tags_through_the_route() -> None:
    app = create_app()
    app.state.sync_control = FakeControl(SNAPSHOT)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/partials/status")

    assert response.status_code == 200
    assert '<time datetime="2026-10-20T12:30:05Z">2026-10-20 12:30:05 UTC</time>' in response.text
