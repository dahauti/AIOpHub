"""Operations Center /centras: real job_runs/record_outcomes screens, owner-only, read-only."""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.config import get_settings
from src.database import get_engine
from src.main import create_app
from src.repositories.job_run_repository import JobRunRepository
from src.repositories.migration_runner import apply_migrations
from src.schemas.operations import JobRunFinish, JobRunStart, RecordOutcome
from tests.test_prototype_i18n import hardcoded_text

Client = httpx.AsyncClient
TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "templates" / "centras"
RUN_A, RUN_B = "a" * 32, "b" * 32
T0 = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
SCREENS = [
    "/centras/",
    "/centras/procesai/tasks_sync",
    f"/centras/paleidimai/{RUN_B}",
    "/centras/irasai/o-b-1",
]


def _outcome(
    outcome_id: str, run_id: str, object_id: str, at: datetime, **fields: object
) -> RecordOutcome:
    trace = "trace-" + run_id[:4]
    base: dict[str, object] = {"object_type": "bc_service_order", "result": "error"}
    return RecordOutcome.model_validate(
        base
        | fields
        | {"outcome_id": outcome_id, "run_id": run_id, "object_id": object_id}
        | {"trace_id": trace, "recorded_at": at}
    )


async def _seed() -> None:
    engine = get_engine()
    await apply_migrations(engine)
    repo = JobRunRepository(engine)
    for run_id, trigger, at in ((RUN_A, "canary", T0), (RUN_B, "manual", T1)):
        start = JobRunStart(
            run_id=run_id,
            process_id="tasks_sync",
            trigger_kind=trigger,
            mode="LIVE",
            profile="onprem_dev",
            started_at=at,
            trace_id="trace-" + run_id[:4],
        )
        await repo.start_run(start)
    failed = {"error_code": "system.bc_unreachable", "error_type": "system"}
    await repo.add_outcome(_outcome("o-a-1", RUN_A, "SO-DEMO-001", T0, **failed))
    no_client = {"error_code": "task.no_client_match", "error_type": "data"}
    params = {"params": {"customer_no": "C00010"}}
    await repo.add_outcome(_outcome("o-a-2", RUN_A, "SO-DEMO-002", T0, **no_client, **params))
    frontu = {"error_code": "system.frontu_error", "error_type": "system", "http_status": 200}
    detail = {"technical_detail": "frontu request failed (status=200): unexpected listing payload"}
    await repo.add_outcome(_outcome("o-b-1", RUN_B, "SO-DEMO-001", T1, **frontu, **detail))
    await repo.add_outcome(_outcome("o-b-2", RUN_B, "SO-DEMO-002", T1, result="ok"))
    await repo.finish_run(
        JobRunFinish(
            run_id=RUN_A,
            status="failed",
            mode="LIVE",
            finished_at=T0.replace(second=21),
            count_failed=1,
            error_code="system.bc_unreachable",
            error_summary="business-central request failed (status=None): ConnectError",
        )
    )
    finish = JobRunFinish(
        run_id=RUN_B, status="succeeded", mode="LIVE", finished_at=T1, count_ok=1, count_failed=1
    )
    await repo.finish_run(finish)
    await engine.dispose()


async def _client(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, host: str = "127.0.0.1"
) -> AsyncIterator[Client]:
    monkeypatch.setenv("CENTRAS_ENABLED", "true" if enabled else "false")
    get_settings.cache_clear()  # the seed already read the settings
    transport = httpx.ASGITransport(app=create_app(), client=(host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest_asyncio.fixture
async def client(temp_database: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client]:
    await _seed()
    async for http in _client(monkeypatch, enabled=True):
        yield http


def _section(html: str, start: str, end: str) -> str:
    return html.split(f'id="{start}"', 1)[1].split(f'id="{end}"', 1)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "host"), [(False, "127.0.0.1"), (True, "10.0.0.5")])
async def test_screens_are_404_unless_enabled_and_local(
    temp_database: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, host: str
) -> None:
    async for http in _client(monkeypatch, enabled, host):
        for path in SCREENS:
            assert (await http.get(path)).status_code == 404
        assert (await http.post("/centras/nuostatos", data={"tema": "dark"})).status_code == 404


@pytest.mark.asyncio
async def test_overview_lists_processes_and_objects_whose_latest_result_is_an_error(
    client: Client,
) -> None:
    html = (await client.get("/centras/")).text

    attention = _section(html, "veiksmo-h", "procesai-h")
    assert "SO-DEMO-001" in attention and "/centras/irasai/o-b-1" in attention
    assert "Frontu neatsakė arba grąžino klaidą (HTTP 200)." in attention
    assert "SO-DEMO-002" not in attention  # its later run succeeded
    processes = html.split('id="procesai-h"', 1)[1]
    assert "Užduočių sinchronizacija" in processes
    assert "Pavyko: 1 · praleista 0 · klaidų 1" in processes
    assert "Transporto priemonių sinchronizacija" in processes
    assert "Paleidimų dar nebuvo." in processes


@pytest.mark.asyncio
async def test_process_page_lists_its_runs_newest_first(client: Client) -> None:
    html = (await client.get("/centras/procesai/tasks_sync")).text

    assert html.index(f"/centras/paleidimai/{RUN_B}") < html.index(f"/centras/paleidimai/{RUN_A}")
    assert "Canary" in html and "Rankinis" in html and "system.bc_unreachable" in html
    assert "21,0 s" in html


@pytest.mark.asyncio
async def test_run_page_shows_its_facts_and_all_its_records(client: Client) -> None:
    html = (await client.get(f"/centras/paleidimai/{RUN_A}")).text

    assert "trace-aaaa" in html and "onprem_dev" in html and "Nepavyko" in html
    assert "Nepavyko prisijungti prie BC \u2013 patikrinkite tinklo ryšį arba VPN." in html
    assert "/centras/irasai/o-a-1" in html and "/centras/irasai/o-a-2" in html
    assert "BC klientas C00010 nerastas Frontu." in html


@pytest.mark.asyncio
async def test_record_page_shows_code_texts_technical_detail_and_every_run(client: Client) -> None:
    html = (await client.get("/centras/irasai/o-b-1")).text

    for fact in ("SO-DEMO-001", "system.frontu_error", "Sistemos klaida", "200 OK", "trace-bbbb"):
        assert fact in html
    assert "Frontu neatsakė arba grąžino klaidą (HTTP 200)." in html
    assert "Frontu did not respond or returned an error (HTTP 200)." in html
    assert "unexpected listing payload" in html
    history = html.split('id="istorija-h"', 1)[1]
    assert f"/centras/paleidimai/{RUN_A}" in history and f"/centras/paleidimai/{RUN_B}" in history


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/centras/procesai/nope", "/centras/paleidimai/nope", "/centras/irasai/nope"]
)
async def test_unknown_ids_are_404(client: Client, path: str) -> None:
    assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_screens_have_no_writing_controls(client: Client) -> None:
    for path in SCREENS:
        html = (await client.get(path)).text
        assert set(re.findall(r'hx-post="([^"]*)"', html)) == {"/centras/nuostatos"}
        assert 'method="post"' not in html.lower() and "hx-delete" not in html
        assert all("disabled" in b for b in re.findall(r"<button[^>]*>", html))


@pytest.mark.asyncio
async def test_preferences_are_a_cookie_only_and_switch_the_language(client: Client) -> None:
    response = await client.post("/centras/nuostatos", data={"tema": "light", "kalba": "en"})

    assert response.status_code == 204
    assert all("Path=/centras" in c for c in response.headers.get_list("set-cookie"))
    html = (await client.get("/centras/")).text
    assert 'lang="en"' in html and 'data-theme="light"' in html and "Task sync" in html


@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.html")), ids=lambda p: p.name)
def test_no_hardcoded_user_facing_text_in_centras_templates(path: Path) -> None:
    assert hardcoded_text(path.read_text(encoding="utf-8")) == []
