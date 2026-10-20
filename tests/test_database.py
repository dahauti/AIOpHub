"""Engine and session factory wiring (no live database required)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_engine, get_session, get_sessionmaker


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
