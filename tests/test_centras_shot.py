"""/centras "Testinis šūvis": form, confirmation dialog, existing canary trigger, wait -> record."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from src.config import get_settings
from src.database import get_engine
from src.domain.canary import CanaryTarget
from src.domain.frontu_models import FrontuTask
from src.domain.task_builders import TASKER_PATCH_FIELDS, tasker_patch
from src.main import create_app
from src.repositories.job_run_repository import JobRunRepository
from src.repositories.migration_runner import apply_migrations
from src.schemas.operations import JobRunFinish, JobRunStart, RecordOutcome

Client = httpx.AsyncClient
ORDER = "SO-DEMO-001"
GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
ENDPOINTS = [
    ("GET", "/centras/testinis"),
    ("POST", "/centras/testinis"),
    ("POST", "/centras/testinis/paleisti"),
    ("GET", f"/centras/testinis/laukia?uzsakymas={ORDER}&nuo=0"),
]


@dataclass
class FakeControl:
    """TaskControlPort double: records canary triggers instead of starting a run."""

    busy: bool = False
    running: bool = False
    calls: list[tuple[CanaryTarget, bool, bool]] = field(default_factory=list)

    def trigger(self) -> bool:
        return False

    def trigger_single(
        self, target: CanaryTarget, force_live: bool = False, force_dry: bool = False
    ) -> bool:
        if self.busy:
            return False
        self.calls.append((target, force_live, force_dry))
        return True

    async def cockpit(self) -> SimpleNamespace:
        return SimpleNamespace(running=self.running)

    async def toggle_dry_run(self) -> None:
        return None


async def _client(
    monkeypatch: pytest.MonkeyPatch,
    control: FakeControl,
    *,
    enabled: bool = True,
    host: str = "127.0.0.1",
) -> AsyncIterator[Client]:
    monkeypatch.setenv("CENTRAS_ENABLED", "true" if enabled else "false")
    get_settings.cache_clear()
    app = create_app()
    app.state.task_control = control
    transport = httpx.ASGITransport(app=app, client=(host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest_asyncio.fixture
async def control() -> FakeControl:
    return FakeControl()


@pytest_asyncio.fixture
async def client(
    temp_database: Path, monkeypatch: pytest.MonkeyPatch, control: FakeControl
) -> AsyncIterator[Client]:
    await apply_migrations(get_engine())
    async for http in _client(monkeypatch, control):
        yield http


async def _canary_run(run_id: str, at: datetime, *, finished: bool = True) -> None:
    repo = JobRunRepository(get_engine())
    start = JobRunStart(
        run_id=run_id,
        process_id="tasks_sync",
        trigger_kind="canary",
        mode="DRY_RUN",
        profile="onprem_dev",
        started_at=at,
        trace_id="trace-" + run_id[:4],
    )
    await repo.start_run(start)
    outcome = {"outcome_id": "o-" + run_id[:4], "run_id": run_id, "object_id": ORDER}
    base = {"object_type": "bc_service_order", "result": "ok", "trace_id": "t", "recorded_at": at}
    await repo.add_outcome(RecordOutcome.model_validate(base | outcome))
    if finished:
        end = JobRunFinish(run_id=run_id, status="succeeded", mode="DRY_RUN", finished_at=at)
        await repo.finish_run(end)


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "host"), [(False, "127.0.0.1"), (True, "10.0.0.5")])
async def test_shot_is_404_unless_enabled_and_local(
    temp_database: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, host: str
) -> None:
    control = FakeControl()
    async for http in _client(monkeypatch, control, enabled=enabled, host=host):
        for method, path in ENDPOINTS:
            data = {"uzsakymas": ORDER, "rezimas": "LIVE"} if method == "POST" else None
            assert (await http.request(method, path, data=data)).status_code == 404
    assert control.calls == []


@pytest.mark.asyncio
async def test_form_defaults_to_dry_run_and_declares_a_writing_action(client: Client) -> None:
    html = (await client.get("/centras/testinis")).text

    assert 'value="DRY_RUN" checked' in html and 'value="LIVE" checked' not in html
    assert "RAŠANTIS VEIKSMAS" in html and "Administruoti" in html
    assert "<dialog" not in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "button", "bar"),
    [("LIVE", "Patvirtinti LIVE", "Režimas: LIVE"), ("DRY_RUN", "Patvirtinti DRY RUN", "DRY RUN")],
)
async def test_first_post_only_shows_the_confirmation_dialog(
    client: Client, control: FakeControl, mode: str, button: str, bar: str
) -> None:
    response = await client.post(
        "/centras/testinis", data={"uzsakymas": f" {ORDER} ", "rezimas": mode}
    )

    dialog = response.text.split("<dialog", 1)[1]
    assert response.status_code == 200 and "data-open-modal" in dialog
    assert button in dialog and bar in dialog and f'value="{ORDER}"' in dialog
    assert "1 BC užsakymas" in dialog and "DEV" in dialog
    assert control.calls == []


@pytest.mark.asyncio
async def test_repeat_link_prefills_the_order_in_dry_run_without_starting(
    client: Client, control: FakeControl
) -> None:
    html = (await client.get(f"/centras/testinis?uzsakymas={ORDER}")).text

    assert f'name="uzsakymas" value="{ORDER}"' in html
    assert 'value="DRY_RUN" checked' in html and "<dialog" not in html
    assert control.calls == []


@pytest.mark.asyncio
async def test_live_dialog_states_exactly_what_is_written(client: Client) -> None:
    response = await client.post("/centras/testinis", data={"uzsakymas": ORDER, "rezimas": "LIVE"})

    dialog = response.text.split("<dialog", 1)[1]
    assert "Frontu: 1 užduotis (sukurti arba atnaujinti)" in dialog
    assert "Item / Resource" in dialog
    header = "DocumentStatus = TASKERYJE, Tasker_ID, Tasker_Document_No, Last_Tasker_Sinhr_Time"
    assert f"BC: užsakymo antraštė ({header}) ir eilutės (Frontu_Integration_Line_GUID)" in dialog


def test_listed_header_fields_are_the_ones_the_canary_patches() -> None:
    patch = tasker_patch(FrontuTask(guid="t-1", code="1"), "TASKERYJE", datetime.now(UTC))

    assert tuple(patch) == TASKER_PATCH_FIELDS


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/centras/testinis", "/centras/testinis/paleisti"])
@pytest.mark.parametrize(
    ("order", "text"),
    [
        ("", "Įveskite BC užsakymo numerį."),
        (GUID, "Frontu užduoties GUID"),
        ("X" * 65, "per ilgas"),
    ],
)
async def test_input_that_is_not_an_order_no_is_rejected(
    client: Client, control: FakeControl, path: str, order: str, text: str
) -> None:
    response = await client.post(path, data={"uzsakymas": order, "rezimas": "LIVE"})

    assert response.status_code == 422 and text in response.text
    assert control.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "live", "dry"), [("DRY_RUN", False, True), ("LIVE", True, False)])
async def test_confirmed_shot_starts_the_existing_canary_in_the_chosen_mode(
    client: Client, control: FakeControl, mode: str, live: bool, dry: bool
) -> None:
    before = int(datetime.now(UTC).timestamp())

    response = await client.post(
        "/centras/testinis/paleisti", data={"uzsakymas": ORDER, "rezimas": mode}
    )

    assert response.status_code == 303
    location = httpx.URL(response.headers["location"])
    assert location.path == "/centras/testinis/laukia"
    assert location.params["uzsakymas"] == ORDER and int(location.params["nuo"]) >= before
    assert control.calls == [(CanaryTarget(kind="order", value=ORDER), live, dry)]


@pytest.mark.asyncio
async def test_busy_canary_is_reported_and_nothing_starts(
    client: Client, control: FakeControl
) -> None:
    control.busy = True

    response = await client.post(
        "/centras/testinis/paleisti", data={"uzsakymas": ORDER, "rezimas": "LIVE"}
    )

    assert response.status_code == 409 and "Jau vykdomas kitas paleidimas" in response.text


@pytest.mark.asyncio
async def test_wait_redirects_to_the_record_of_this_shots_finished_canary(client: Client) -> None:
    shot_at = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    await _canary_run("e" * 32, shot_at.replace(hour=7))  # an older canary of the same order
    await _canary_run("f" * 32, shot_at.replace(second=3))

    nuo = int(shot_at.timestamp())
    response = await client.get(f"/centras/testinis/laukia?uzsakymas={ORDER}&nuo={nuo}")

    assert response.status_code == 303
    assert response.headers["location"] == "/centras/irasai/o-ffff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "refresh", "text"),
    [(True, True, "Vykdoma: SO-DEMO-001"), (False, False, "Rezultato nėra")],
)
async def test_wait_refreshes_while_running_and_explains_a_missing_result(
    client: Client, control: FakeControl, running: bool, refresh: bool, text: str
) -> None:
    control.running = running
    await _canary_run("d" * 32, datetime(2026, 10, 23, 8, 0, 5, tzinfo=UTC), finished=False)

    nuo = int(datetime(2026, 10, 23, 8, 0, tzinfo=UTC).timestamp())
    response = await client.get(f"/centras/testinis/laukia?uzsakymas={ORDER}&nuo={nuo}")

    assert response.status_code == 200 and text in response.text
    assert ('http-equiv="refresh"' in response.text) is refresh
