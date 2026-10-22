"""Numbered SQL migrations (ADR 0003): applied once, re-runnable, tampering stops startup."""

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.repositories.migration_runner import (
    MIGRATIONS_DIR,
    MigrationChecksumError,
    apply_migrations,
    split_statements,
)

OPS_TABLES = {"processes", "job_runs", "record_outcomes", "schema_migrations"}


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    db = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'ops.db').as_posix()}")
    yield db
    await db.dispose()


@pytest.fixture
def migrations(tmp_path: Path) -> Path:
    """A private copy of db/migrations that a test may change."""
    directory = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, directory)
    return directory


async def _scalars(engine: AsyncEngine, sql: str) -> list[str]:
    async with engine.connect() as connection:
        return [str(row[0]) for row in await connection.execute(text(sql))]


@pytest.mark.asyncio
async def test_first_run_applies_0001_and_seeds_the_process_registry(engine: AsyncEngine) -> None:
    applied = await apply_migrations(engine)

    tables = await _scalars(engine, "SELECT name FROM sqlite_master WHERE type = 'table'")
    assert applied == ["0001"]
    assert set(tables) >= OPS_TABLES
    assert await _scalars(engine, "SELECT id FROM processes ORDER BY id") == [
        "tasks_sync",
        "workspaces_vehicles",
    ]
    assert await _scalars(engine, "SELECT version FROM schema_migrations") == ["0001"]


@pytest.mark.asyncio
async def test_second_run_applies_nothing_and_keeps_rows(engine: AsyncEngine) -> None:
    await apply_migrations(engine)

    assert await apply_migrations(engine) == []
    assert await _scalars(engine, "SELECT COUNT(*) FROM processes") == ["2"]
    assert await _scalars(engine, "SELECT COUNT(*) FROM schema_migrations") == ["1"]


@pytest.mark.asyncio
async def test_new_file_is_applied_after_existing_ones(
    engine: AsyncEngine, migrations: Path
) -> None:
    await apply_migrations(engine, migrations)
    (migrations / "0002_extra.sql").write_text(
        "CREATE TABLE IF NOT EXISTS extra (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )

    assert await apply_migrations(engine, migrations) == ["0002"]
    assert await _scalars(engine, "SELECT version FROM schema_migrations ORDER BY version") == [
        "0001",
        "0002",
    ]


@pytest.mark.asyncio
async def test_changed_applied_migration_stops_with_clear_error(
    engine: AsyncEngine, migrations: Path
) -> None:
    await apply_migrations(engine, migrations)
    target = migrations / "0001_operations_center.sql"
    target.write_text(target.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")

    with pytest.raises(MigrationChecksumError) as error:
        await apply_migrations(engine, migrations)

    message = str(error.value)
    assert "0001_operations_center.sql" in message
    assert "already applied" in message and "new numbered file" in message


@pytest.mark.asyncio
async def test_failed_migration_is_not_recorded(engine: AsyncEngine, migrations: Path) -> None:
    await apply_migrations(engine, migrations)
    (migrations / "0002_broken.sql").write_text(
        "CREATE TABLE ok_part (id TEXT);\nTHIS IS NOT SQL;\n", encoding="utf-8"
    )

    with pytest.raises(Exception, match="syntax error"):
        await apply_migrations(engine, migrations)

    tables = await _scalars(engine, "SELECT name FROM sqlite_master WHERE type = 'table'")
    assert "ok_part" not in tables
    assert await _scalars(engine, "SELECT version FROM schema_migrations") == ["0001"]


def test_split_statements_drops_comments_and_blank_parts() -> None:
    sql = "-- header\nCREATE TABLE a (id TEXT); -- trailing\n\nINSERT INTO a VALUES ('x');\n"

    assert split_statements(sql) == ["CREATE TABLE a (id TEXT)", "INSERT INTO a VALUES ('x')"]


@pytest.mark.asyncio
async def test_init_db_applies_migrations(temp_database: Path) -> None:
    from src.database import get_engine, init_db

    await init_db()

    tables = await _scalars(get_engine(), "SELECT name FROM sqlite_master WHERE type = 'table'")
    assert set(tables) >= OPS_TABLES
    await get_engine().dispose()
