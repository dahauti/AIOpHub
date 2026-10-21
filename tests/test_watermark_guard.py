"""Cold-start guard: 50-page ceiling, 24h version floor and `--bootstrap-watermark`."""

from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from src import cli
from src.adapters.resilience import ExternalServiceError
from src.clients.frontu_tasks_client import MAX_PAGES
from src.config import get_settings
from src.database import get_engine
from src.domain.frontu_models import FrontuTask
from src.services.task_inbound import COLD_START_LOOKBACK_SECONDS, STREAM, TaskInboundService
from src.services.task_runner import run_bootstrap
from src.storage.task_repository import TaskRepository
from tests.fakes import RecordingHandler
from tests.task_fakes import NOW, FakeBC, FakeFrontu, Stores, stores
from tests.task_runner_fakes import MockBackends, backends
from tests.test_frontu_tasks_client import _client

__all__ = ["backends", "stores"]

FLOOR = int(NOW.timestamp()) - COLD_START_LOOKBACK_SECONDS


def _endless(request: httpx.Request) -> httpx.Response:
    page = int(request.url.params["page"])
    items: list[JsonValue] = [{"guid": f"t-{page}", "version": 1000 + page}]
    return httpx.Response(200, json={"items": items, "next_page": page + 1})


def _service(bc: FakeBC, frontu: FakeFrontu, stores: Stores) -> TaskInboundService:
    return TaskInboundService(bc, frontu, stores.watermarks, stores.states, now=lambda: NOW)


@pytest.mark.asyncio
async def test_delta_fails_closed_after_the_50_page_ceiling() -> None:
    mock = RecordingHandler(_endless)

    with pytest.raises(ExternalServiceError, match=r"page ceiling of 50.*bootstrap-watermark"):
        await _client(mock).fetch_task_delta(0)

    assert MAX_PAGES == 50 and len(mock.requests) == 50


@pytest.mark.asyncio
async def test_scan_reads_at_most_50_pages_and_returns_highest_version() -> None:
    mock = RecordingHandler(_endless)

    assert await _client(mock).scan_latest_version(0) == 1050
    assert len(mock.requests) == 50


@pytest.mark.asyncio
async def test_listing_ending_exactly_on_page_50_is_complete() -> None:
    def fifty(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        items: list[JsonValue] = [{"guid": f"t-{page}"}]
        return httpx.Response(
            200, json={"items": items, "next_page": 0 if page == 50 else page + 1}
        )

    assert len(await _client(RecordingHandler(fifty)).fetch_task_delta(0)) == 50


@pytest.mark.asyncio
async def test_cold_start_polls_last_24h_not_version_zero(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()

    await _service(bc, frontu, stores).run()

    assert frontu.versions == [FLOOR] and FLOOR > 0
    assert await stores.watermarks.get_version_watermark(STREAM) is None


@pytest.mark.asyncio
async def test_stored_watermark_wins_over_cold_start(stores: Stores) -> None:
    await stores.watermarks.set_version_watermark(STREAM, 1234)
    frontu = FakeFrontu()

    await _service(FakeBC(), frontu, stores).run()

    assert frontu.versions == [1234]


@pytest.mark.asyncio
async def test_bootstrap_stamps_latest_remote_version_without_syncing(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    frontu.delta = [
        FrontuTask(guid="a", version=FLOOR + 5),
        FrontuTask(guid="b", version=FLOOR + 9),
    ]

    assert await _service(bc, frontu, stores).bootstrap_watermark() == FLOOR + 9

    assert frontu.scans == [FLOOR] and frontu.versions == [] and frontu.upserts == []
    assert bc.header_patches == [] and bc.created_lines == []
    assert await stores.watermarks.get_version_watermark(STREAM) == FLOOR + 9


@pytest.mark.asyncio
async def test_bootstrap_with_no_remote_tasks_stamps_the_floor(stores: Stores) -> None:
    assert await _service(FakeBC(), FakeFrontu(), stores).bootstrap_watermark() == FLOOR
    assert await stores.watermarks.get_version_watermark(STREAM) == FLOOR


@pytest.mark.asyncio
async def test_run_bootstrap_stamps_real_watermark_even_in_dry_run(
    backends: MockBackends,
) -> None:
    settings = get_settings()
    assert settings.sync_dry_run is True

    baseline = await run_bootstrap(settings)

    try:
        stored = await TaskRepository(get_engine()).get_version_watermark(STREAM)
    finally:
        await get_engine().dispose()
    assert baseline is not None and stored == baseline
    assert backends.writes == []
    assert {r.url.path.rsplit("/", 1)[-1] for r in backends.requests} == {"tasks"}


def test_cli_bootstrap_prints_baseline(
    backends: MockBackends, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))

    assert cli.main(["tasks", "--bootstrap-watermark"]) == 0

    banner, line = capsys.readouterr().out.splitlines()
    assert banner.startswith("[AIOpHUB] Env: DEV")
    assert line.startswith("tasks --bootstrap-watermark: baseline version=")


@pytest.mark.parametrize("argv", [["tasks", "--bootstrap-watermark", "--sync"]])
def test_bootstrap_is_exclusive_with_other_modes(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
