"""Engine, WAL pragmas, data-dir creation and init_db against a temp SQLite file."""

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_engine, get_session, get_sessionmaker, init_db, safe_database_label


@pytest.mark.usefixtures("temp_database")
def test_engine_uses_aiosqlite_and_creates_data_directory(temp_database: Path) -> None:
    engine = get_engine()

    assert engine.url.drivername == "sqlite+aiosqlite"
    assert temp_database.parent.is_dir()
    assert get_engine() is engine


@pytest.mark.usefixtures("temp_database")
def test_sessionmaker_is_bound_and_keeps_objects_after_commit() -> None:
    factory = get_sessionmaker()

    assert factory.kw["bind"] is get_engine()
    assert factory.kw["expire_on_commit"] is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("temp_database")
async def test_connections_enable_wal_and_foreign_keys() -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        journal = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
        foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
    await engine.dispose()

    assert journal == "wal"
    assert foreign_keys == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("temp_database")
async def test_init_db_creates_tables_and_index_idempotently(temp_database: Path) -> None:
    await init_db()
    await init_db()

    async with get_engine().connect() as connection:
        tables = await connection.run_sync(lambda c: sorted(inspect(c).get_table_names()))
        indexes = await connection.run_sync(lambda c: inspect(c).get_indexes("sync_audit_log"))
    await get_engine().dispose()

    assert tables == ["sync_audit_log", "sync_config", "sync_fingerprints", "sync_states"]
    assert [ix["name"] for ix in indexes] == ["idx_audit_entity"]
    assert temp_database.is_file()


@pytest.mark.asyncio
@pytest.mark.usefixtures("temp_database")
async def test_get_session_yields_async_session() -> None:
    sessions = get_session()
    session = await anext(sessions)

    assert isinstance(session, AsyncSession)
    await sessions.aclose()


def test_safe_database_label_masks_credentials(
    monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    assert safe_database_label() == "sqlite+aiosqlite:///./data/sync.db"
