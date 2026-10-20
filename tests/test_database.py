"""Engine and session factory wiring (no live database required)."""

import pytest
from sqlalchemy import create_mock_engine, make_url
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.ddl import ExecutableDDLElement

from src.database import (
    create_schema_statement,
    get_engine,
    get_session,
    get_sessionmaker,
    safe_database_label,
)
from src.models.audit import Base

PG_DIALECT = make_url("postgresql://").get_dialect()()


@pytest.fixture(autouse=True)
def _reset_db_caches(isolated_env: None) -> None:
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def test_engine_uses_asyncpg_driver_from_settings() -> None:
    engine = get_engine()

    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.database == "aiophub"
    assert get_engine() is engine


def test_sessionmaker_is_bound_and_keeps_objects_after_commit() -> None:
    factory = get_sessionmaker()

    assert factory.kw["bind"] is get_engine()
    assert factory.kw["expire_on_commit"] is False
    assert get_sessionmaker() is factory


@pytest.mark.asyncio
async def test_get_session_yields_async_session() -> None:
    sessions = get_session()
    session = await anext(sessions)

    assert isinstance(session, AsyncSession)
    await sessions.aclose()


def test_schema_statement_is_idempotent() -> None:
    ddl = str(create_schema_statement().compile(dialect=PG_DIALECT))

    assert ddl == "CREATE SCHEMA IF NOT EXISTS sync_engine"


def test_create_all_emits_spec_tables_and_index() -> None:
    emitted: list[str] = []

    def capture(sql: ExecutableDDLElement, *_args: object, **_kwargs: object) -> None:
        emitted.append(" ".join(str(sql.compile(dialect=PG_DIALECT)).split()))

    mock_engine = create_mock_engine(make_url("postgresql://"), capture)
    Base.metadata.create_all(mock_engine, checkfirst=False)

    created = [s for s in emitted if s.startswith("CREATE TABLE")]
    assert sorted(s.split(" (")[0] for s in created) == [
        "CREATE TABLE sync_engine.sync_audit_log",
        "CREATE TABLE sync_engine.sync_fingerprints",
        "CREATE TABLE sync_engine.sync_states",
    ]
    assert "CREATE INDEX idx_audit_entity ON sync_engine.sync_audit_log (entity_key)" in emitted


def test_safe_database_label_hides_password(isolated_env: None) -> None:
    label = safe_database_label()

    assert label == "postgresql+asyncpg://user:***@localhost:5432/aiophub"
    assert "password" not in label
