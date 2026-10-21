"""Unified cockpit: executive header badges, system status, scope pills and fixed footprints."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.adapters.link_health import LinkOutcome
from src.api.cockpit import system_status
from src.main import create_app
from src.schemas.sync import AuditRecord
from src.services.cockpit_models import TaskCockpit
from src.services.link_status import LinkPanel, build_panel
from tests.ui_fakes import NOW, SNAPSHOT, FakeControl, FakeTaskControl

CSS = (Path(__file__).resolve().parents[1] / "src" / "static" / "hub.css").read_text("utf-8")
Clients = tuple[FakeControl, FakeTaskControl, httpx.AsyncClient]


class FakeLinks:
    def __init__(self) -> None:
        self.state = LinkPanel()

    async def panel(self) -> LinkPanel:
        return self.state


def _outcome(service: str, status_code: int | None) -> LinkOutcome:
    return LinkOutcome(service=service, status_code=status_code, checked_at=NOW)


FRONTU_DOWN = build_panel(
    {"business-central": _outcome("business-central", 200), "frontu": _outcome("frontu", 503)}
)


@pytest.fixture
def links() -> FakeLinks:
    return FakeLinks()


@pytest_asyncio.fixture
async def clients(links: FakeLinks) -> AsyncIterator[Clients]:
    app = create_app()
    rows = [AuditRecord(created_at=NOW, entity_key="tasks_sync", action="TASK_RUN")]
    vehicles = FakeControl(SNAPSHOT.model_copy(update={"recent": [*rows, *SNAPSHOT.recent]}))
    tasks = FakeTaskControl()
    app.state.sync_control, app.state.task_control = vehicles, tasks
    app.state.link_health = links
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield vehicles, tasks, client


def _task(**update: object) -> TaskCockpit:
    return FakeTaskControl().state.model_copy(update=update)


@pytest.mark.parametrize(
    ("vehicles", "task", "expected"),
    [
        ("IDLE", _task(), "OPERATIONAL"),
        ("RUNNING", _task(), "SYNCING"),
        ("IDLE", _task(running=True, status="RUNNING"), "SYNCING"),
        ("FAILED", _task(), "DEGRADED"),
        ("RUNNING", _task(status="STALE"), "DEGRADED"),
        ("IDLE", None, "OPERATIONAL"),
    ],
)
def test_system_status_rolls_up_both_streams(
    vehicles: str, task: TaskCockpit | None, expected: str
) -> None:
    assert system_status(vehicles, task) == expected


@pytest.mark.asyncio
async def test_header_carries_title_env_and_system_badges(clients: Clients) -> None:
    _, _, client = clients

    html = (await client.get("/")).text

    assert '<span class="hub-mark">ALWARK</span>' in html and "INTEGRATION ENGINE" in html
    assert '<span aria-current="page" class="hub-tab hub-tab-active">Integracijos</span>' in html
    assert html.count('aria-disabled="true"') == 5
    assert (
        '<span id="env-badge" title="Active environment (APP_ENV)" class="pill env-dev">DEV<'
        in html
    )
    for link in ("link-bc", "link-frontu"):
        assert f'<span id="{link}" title="No calls recorded yet" class="hub-indicator">' in html
    assert html.count('<span class="dot dot-unknown"></span>') == 2
    assert 'id="link-gateway"' not in html  # FakeTaskControl has no BC profile
    assert 'href="#task-card"' in html and 'href="#audit-zone"' in html
    assert html.index('id="cockpit-header"') < html.index('id="overview"')


@pytest.mark.asyncio
async def test_header_dots_follow_each_link_independently(
    clients: Clients, links: FakeLinks
) -> None:
    _, tasks, client = clients
    tasks.state = tasks.state.model_copy(update={"app_env": "prod", "bc_profile": "onprem"})
    links.state = FRONTU_DOWN

    html = (await client.get("/partials/header")).text

    assert html.lstrip().startswith('<header id="cockpit-header"')
    assert 'hx-trigger="sync-state-changed from:body" hx-swap="morph:outerHTML"' in html
    assert 'class="pill env-prod">PROD<' in html
    for link, dot in (("gateway", "ok"), ("bc", "ok"), ("frontu", "failing")):
        start = html.index(f'id="link-{link}"')
        assert (
            f'<span class="dot dot-{dot}"></span>'
            in html[start : html.index("</span>", start) + 40]
        )


@pytest.mark.asyncio
async def test_vehicle_column_uses_status_pill(clients: Clients) -> None:
    _, _, client = clients

    html = (await client.get("/partials/status")).text

    assert '<span id="vehicle-status" class="pill pill-wide pill-idle">IDLE</span>' in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "active", "visible", "hidden"),
    [
        ("", "All", "SN-0001", None),
        ("TASKS", "Tasks", "tasks_sync", "SN-0001"),
        ("VEHICLES", "Vehicles", "SN-0001", "tasks_sync"),
        ("ALL_ERRORS", "Errors Only", "SN-0002", "tasks_sync"),
    ],
)
async def test_scope_pills_filter_and_mark_the_active_scope(
    clients: Clients, action: str, active: str, visible: str, hidden: str | None
) -> None:
    _, _, client = clients

    html = (await client.get(f"/partials/audit?action={action}&range=all")).text

    pills = html[html.index('<nav id="audit-pills"') : html.index("</nav>")]
    assert pills.count('class="filter-pill') == 4
    assert f'aria-pressed="true" class="filter-pill filter-pill-active">{active}</button>' in pills
    assert visible in html
    if hidden is not None:
        assert f'title="{hidden}"' not in html


def test_dynamic_badges_and_buttons_have_fixed_footprints() -> None:
    for rule in (".pill {", ".pill-wide { min-width:", ".btn-fixed { min-width:", ".filter-pill {"):
        assert rule in CSS
    assert "height: 1.5rem" in CSS and "white-space: nowrap" in CSS


@pytest.mark.asyncio
async def test_diagnostic_cards_report_their_own_link_only(
    clients: Clients, links: FakeLinks
) -> None:
    _, tasks, client = clients
    tasks.state = tasks.state.model_copy(
        update={
            "frontu_base_url": "https://alwark.frontu.com/client/api/v2",
            "bc_company": "Copy LT 20241223",
            "bc_profile": "onprem",
            "frontu_version": 1792496431,
            "status": "FAILED",
        }
    )
    links.state = FRONTU_DOWN

    page = (await client.get("/")).text
    strip = (await client.get("/partials/diagnostics")).text

    assert strip.lstrip().startswith('<section id="overview"')
    assert 'hx-trigger="sync-state-changed from:body"' in strip
    assert '<dd id="diag-frontu-version" class="diag-value mono-id">1792496431</dd>' in strip
    assert '<dd id="diag-bc-company" class="diag-value mono-id">Copy LT 20241223</dd>' in strip
    assert "https://alwark.frontu.com/client/api/v2" in strip and ">ONPREM<" in strip
    frontu = strip[strip.index('id="diag-frontu-health"') :]
    bc = strip[strip.index('id="diag-bc-health"') :]
    assert frontu.split("</span>")[0].endswith('class="pill pill-wide pill-failed">FAILING')
    assert bc.split("</span>")[0].endswith('class="pill pill-wide pill-ok">OPERATIONAL')
    assert "HTTP 503" in strip and "HTTP 200" in strip
    assert 'id="link-gateway"' in page
