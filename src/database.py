"""Postgres async engine, session factory (asyncpg driver) and schema bootstrap CLI.

Run `python -m src.database` to create the `sync_engine` schema and tables (idempotent).
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema

from src.config import get_settings
from src.models.audit import SCHEMA, Base

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine; created lazily on first use."""
    return create_async_engine(str(get_settings().database_url), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory bound to the shared engine."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield a session scoped to one unit of work (FastAPI dependency)."""
    async with get_sessionmaker()() as session:
        yield session


def create_schema_statement() -> CreateSchema:
    return CreateSchema(SCHEMA, if_not_exists=True)


def safe_database_label() -> str:
    """DATABASE_URL with the password masked, safe for logs."""
    return make_url(str(get_settings().database_url)).render_as_string(hide_password=True)


async def init_schema() -> None:
    """Create the sync_engine schema and any missing tables/indexes in one transaction."""
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(create_schema_statement())
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger.info("Initialising schema %r on %s", SCHEMA, safe_database_label())
    asyncio.run(init_schema())
    logger.info("Schema %r and tables are ready", SCHEMA)


if __name__ == "__main__":
    main()
