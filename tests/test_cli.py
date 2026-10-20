"""Headless batch runner: argument parsing, summary output, lock bailout and exit codes."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src import cli
from src.config import Settings, get_settings
from src.database import get_sessionmaker, init_db
from src.pipelines.sync_vehicles import INITIAL_WINDOW, MODULE, sync_vehicles_batch
from src.repositories.sync_repository import SqlSyncRepository
from src.schemas.sync import SyncRunResult

RESULT = SyncRunResult(
    processed=3, skipped=2, failed=1, planned=0, cursor=datetime(2026, 10, 20, tzinfo=UTC)
)


class _Engine:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def quiet_cli(monkeypatch: pytest.MonkeyPatch, isolated_env: None) -> None:
    """No log files and no real engine for the CLI wrapper tests."""
    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))
    monkeypatch.setattr(cli, "get_engine", lambda: _Engine())


def _batch_returning(result: SyncRunResult | None) -> object:
    async def fake(_settings: Settings) -> SyncRunResult | None:
        return result

    return fake


@pytest.mark.parametrize("argv", [[], ["sync"], ["sync", "--single"], ["--batch"]])
def test_invalid_arguments_exit_with_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2


def test_parser_accepts_sync_batch() -> None:
    args = cli.build_parser().parse_args(["sync", "--batch"])

    assert (args.command, args.batch) == ("sync", True)


def test_successful_run_prints_one_summary_line(
    quiet_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "sync_vehicles_batch", _batch_returning(RESULT))

    assert cli.main(["sync", "--batch"]) == 0

    [line] = capsys.readouterr().out.splitlines()
    assert line.startswith("sync --batch [DRY RUN]: ")
    for fragment in ("processed=3", "planned=0", "skipped=2", "errors=1", "elapsed="):
        assert fragment in line


def test_locked_module_logs_warning_and_exits_zero(
    quiet_cli: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(cli, "sync_vehicles_batch", _batch_returning(None))

    assert cli.main(["sync", "--batch"]) == 0

    assert capsys.readouterr().out == ""
    assert any("another run" in r.getMessage() for r in caplog.records)


def test_unhandled_exception_exits_one(quiet_cli: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_settings: Settings) -> SyncRunResult | None:
        raise RuntimeError("BC unreachable")

    monkeypatch.setattr(cli, "sync_vehicles_batch", boom)

    assert cli.main(["sync", "--batch"]) == 1


@pytest.mark.asyncio
async def test_batch_bails_out_when_sync_states_claims_the_module(temp_database: Path) -> None:
    await init_db()
    repository = SqlSyncRepository(get_sessionmaker(), MODULE)
    now = datetime.now(UTC)
    assert await repository.try_acquire(now, now - INITIAL_WINDOW)

    assert await sync_vehicles_batch(get_settings()) is None

    snapshot = await repository.dashboard_snapshot(now - timedelta(hours=1), limit=5)
    assert snapshot.status == "RUNNING" and snapshot.recent == []
