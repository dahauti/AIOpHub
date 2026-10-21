"""run_tasks / `tasks` CLI end to end: tasks_sync lock, dry-run bypass and live writes."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src import cli
from src.adapters.resilience import ExternalServiceError
from src.config import get_settings
from src.database import get_engine, init_db
from src.services.task_runner import run_tasks, task_lock
from src.storage.task_repository import TaskRepository
from src.storage.task_state_repository import TaskStateRepository
from tests.task_runner_fakes import MockBackends, audited_operations, backends

__all__ = ["backends"]


async def _lock_status() -> str:
    try:
        return (await task_lock().dashboard_snapshot(datetime.now(UTC), limit=1)).status
    finally:
        await get_engine().dispose()


async def _hold_lock() -> None:
    await init_db()
    try:
        now = datetime.now(UTC)
        assert await task_lock().try_acquire(now, now)
    finally:
        await get_engine().dispose()


@pytest.mark.asyncio
async def test_overlapping_run_is_rejected_without_any_http(backends: MockBackends) -> None:
    settings = get_settings()
    await _hold_lock()

    assert await run_tasks(settings, "sync") is None

    assert backends.requests == []
    assert await _lock_status() == "RUNNING"


@pytest.mark.asyncio
async def test_dry_run_reads_but_never_writes_and_audits_every_mutation(
    backends: MockBackends,
) -> None:
    settings = get_settings()
    assert settings.sync_dry_run is True

    report = await run_tasks(settings, "sync")

    assert report is not None and report.dry_run is True
    assert [(run.direction, run.result.synced) for run in report.runs] == [
        ("outbound", 1),
        ("inbound", 1),
    ]
    assert backends.requests and backends.writes == []
    assert sorted(await audited_operations()) == [
        "bc.create_order_line",
        "bc.update_order_header",
        "bc.update_order_header",
        "bc.update_order_line",
        "frontu.push_material",
        "frontu.upsert_task",
    ]
    engine = get_engine()
    try:
        watermarks = TaskRepository(engine)
        assert await watermarks.get_raw_watermark("operative_tasks_bc") is None
        assert await watermarks.get_version_watermark("operative_tasks_frontu") is None
        assert await TaskStateRepository(engine).get_by_order("SO00001") is None
    finally:
        await engine.dispose()
    assert await _lock_status() == "IDLE"


@pytest.mark.asyncio
async def test_live_run_sends_writes_and_persists_state(
    backends: MockBackends, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SYNC_DRY_RUN", "false")
    get_settings.cache_clear()
    settings = get_settings()

    report = await run_tasks(settings, "outbound")

    assert report is not None and report.dry_run is False
    assert [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in backends.writes] == [
        ("POST", "tasks"),
        ("POST", "materials"),
        ("PATCH", "TG_Lines(Document_Type='Order',DocumentNo='SO00001',LineNo=10000)"),
        ("PATCH", "ServiceOrders(Document_Type='Order',No='SO00001')"),
    ]
    assert await audited_operations() == []
    engine = get_engine()
    try:
        state = await TaskStateRepository(engine).get_by_order("SO00001")
    finally:
        await engine.dispose()
    assert state is not None and state.sync_status == "SYNCED"


@pytest.mark.asyncio
async def test_failed_run_releases_lock_as_failed(backends: MockBackends) -> None:
    backends.fail_bc_reads = True
    settings = get_settings()

    with pytest.raises(ExternalServiceError):
        await run_tasks(settings, "outbound")

    assert await _lock_status() == "FAILED"
    assert await run_tasks(settings, "inbound") is not None


def test_cli_tasks_sync_runs_end_to_end_in_dry_run(
    backends: MockBackends, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))

    assert cli.main(["tasks", "--sync"]) == 0

    banner, *lines = capsys.readouterr().out.splitlines()
    assert banner.startswith("[AIOpHUB] Env: DEV")
    assert [line.split(":")[0] for line in lines] == [
        "tasks --outbound [DRY RUN]",
        "tasks --inbound [DRY RUN]",
    ]
    assert "processed=1 synced=1 skipped=0 errors=0" in lines[0]
    assert backends.writes == []


def test_cli_exits_zero_without_output_when_locked(
    backends: MockBackends,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))
    asyncio.run(_hold_lock())

    assert cli.main(["tasks", "--inbound"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == []
    assert backends.requests == []
    assert any("another run" in record.getMessage() for record in caplog.records)
