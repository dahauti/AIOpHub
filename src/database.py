"""Embedded SQLite (aiosqlite, WAL mode) engine, session factory and init CLI.

Run `python -m src.database` to create the sync tables (idempotent).
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import URL, event, make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry

from src.config import get_settings
from src.models.audit import Base

logger = logging.getLogger(__name__)


def set_sqlite_pragma(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    """Enable WAL journaling and foreign-key enforcement on every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _ensure_parent_directory(url: URL) -> None:
    database = url.database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine; creates the database directory on first use."""
    url = make_url(get_settings().database_url)
    _ensure_parent_directory(url)
    engine = create_async_engine(url, pool_pre_ping=True)
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    return engine


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory bound to the shared engine."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield a session scoped to one unit of work (FastAPI dependency)."""
    async with get_sessionmaker()() as session:
        yield session


def safe_database_label() -> str:
    """DATABASE_URL with any password masked, safe for logs."""
    return make_url(get_settings().database_url).render_as_string(hide_password=True)


async def init_db() -> None:
    """Create any missing tables and indexes."""
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger.info("Initialising database at %s", safe_database_label())
    asyncio.run(init_db())
    logger.info("Database tables are ready")


if __name__ == "__main__":
    main()
