"""Manual cursor control: SQL repository semantics and the runner's in-process guard."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from src.database import get_engine, get_sessionmaker, init_db
from src.pipelines.scheduler import SyncRunner
from src.repositories.sync_repository import SqlSyncRepository
from src.schemas.sync import SyncRunResult
from tests.repository_fakes import FakeConfigStore, FakeSyncRepository

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(days=10)


@pytest_asyncio.fixture
async def repo(temp_database: Path) -> AsyncIterator[SqlSyncRepository]:
    await init_db()
    yield SqlSyncRepository(get_sessionmaker(), "workspaces_vehicles")
    await get_engine().dispose()


@pytest.mark.asyncio
async def test_set_cursor_creates_state_and_can_rewind(repo: SqlSyncRepository) -> None:
    assert await repo.set_cursor(NOW, NOW)
    assert await repo.get_cursor() == NOW

    assert await repo.set_cursor(EARLIER, NOW)
    assert await repo.get_cursor() == EARLIER
    assert (await repo.dashboard_snapshot(NOW, 5)).status == "IDLE"


@pytest.mark.asyncio
async def test_set_cursor_is_refused_while_a_fresh_run_holds_the_module(
    repo: SqlSyncRepository,
) -> None:
    assert await repo.try_acquire(NOW, EARLIER)

    assert not await repo.set_cursor(NOW, NOW)
    assert await repo.get_cursor() == EARLIER

    await repo.release("IDLE", NOW)
    assert await repo.set_cursor(NOW, NOW)


@pytest.mark.asyncio
async def test_set_cursor_overrides_a_stale_running_claim(repo: SqlSyncRepository) -> None:
    assert await repo.try_acquire(EARLIER, EARLIER)

    assert await repo.set_cursor(NOW, NOW)


class _Pipeline:
    def __init__(self, running: bool) -> None:
        self.is_running = running
        self.dry_run = True

    async def run(self) -> SyncRunResult | None:
        return None


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.asyncio
async def test_runner_refuses_cursor_change_during_in_process_run(running: bool) -> None:
    repository = FakeSyncRepository(cursor=EARLIER)
    runner = SyncRunner(_Pipeline(running), repository, FakeConfigStore(), clock=lambda: NOW)

    changed = await runner.set_cursor(NOW)

    assert changed is not running
    assert repository.cursor == (EARLIER if running else NOW)
