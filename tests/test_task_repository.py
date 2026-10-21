"""TaskRepository integration tests against a real temp SQLite database."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.storage.task_repository import TaskRepository

STREAM = "operative_tasks_bc"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    db_path = tmp_path / "aio_state.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(engine: AsyncEngine) -> TaskRepository:
    repository = TaskRepository(engine)
    await repository.migrate()
    return repository


@pytest.mark.asyncio
async def test_migrate_creates_tables_idempotently(
    engine: AsyncEngine, repo: TaskRepository
) -> None:
    await repo.migrate()

    async with engine.connect() as connection:
        rows = await connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in rows}
    assert {"task_sync_state", "task_line_sync_state", "sync_watermarks"} <= tables


@pytest.mark.asyncio
async def test_missing_watermark_returns_none(repo: TaskRepository) -> None:
    assert await repo.get_watermark(STREAM) is None


@pytest.mark.asyncio
async def test_watermark_subtracts_default_fifteen_minute_buffer(repo: TaskRepository) -> None:
    await repo.set_watermark(STREAM, NOW)

    assert await repo.get_watermark(STREAM) == NOW - timedelta(minutes=15)
    assert await repo.get_raw_watermark(STREAM) == NOW


@pytest.mark.asyncio
async def test_watermark_uses_configured_buffer_and_keeps_it_on_update(
    repo: TaskRepository,
) -> None:
    await repo.set_watermark(STREAM, NOW, safety_buffer_seconds=60)
    await repo.set_watermark(STREAM, NOW + timedelta(hours=1))

    assert await repo.get_watermark(STREAM) == NOW + timedelta(minutes=59)


@pytest.mark.asyncio
async def test_repository_default_buffer_is_configurable(engine: AsyncEngine) -> None:
    repository = TaskRepository(engine, default_safety_buffer_seconds=300)
    await repository.migrate()
    await repository.set_watermark("master_vehicles", NOW)

    assert await repository.get_watermark("master_vehicles") == NOW - timedelta(minutes=5)


@pytest.mark.asyncio
async def test_streams_are_isolated(repo: TaskRepository) -> None:
    await repo.set_watermark(STREAM, NOW)
    await repo.set_watermark("operative_tasks_frontu", NOW - timedelta(days=1), 0)

    assert await repo.get_watermark("operative_tasks_frontu") == NOW - timedelta(days=1)
    assert await repo.get_watermark(STREAM) == NOW - timedelta(minutes=15)


@pytest.mark.asyncio
async def test_watermark_is_normalised_to_utc(repo: TaskRepository) -> None:
    local = NOW.astimezone(timezone(timedelta(hours=3)))
    await repo.set_watermark(STREAM, local, safety_buffer_seconds=0)

    stored = await repo.get_watermark(STREAM)
    assert stored == NOW
    assert stored is not None and stored.tzinfo is UTC


@pytest.mark.asyncio
async def test_rejects_naive_watermark_and_negative_buffer(repo: TaskRepository) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await repo.set_watermark(STREAM, datetime(2026, 10, 21))
    with pytest.raises(ValueError, match="non-negative"):
        await repo.set_watermark(STREAM, NOW, safety_buffer_seconds=-1)


@pytest.mark.asyncio
async def test_version_watermark_round_trips_without_buffer(repo: TaskRepository) -> None:
    assert await repo.get_version_watermark("operative_tasks_frontu") is None

    await repo.set_version_watermark("operative_tasks_frontu", 1042)
    await repo.set_version_watermark("operative_tasks_frontu", 1050)

    assert await repo.get_version_watermark("operative_tasks_frontu") == 1050
