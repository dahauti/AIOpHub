"""Runtime dashboard toggles in `sync_config`; a module without a row uses the `.env` defaults."""

from typing import Protocol

from sqlalchemy import func, not_, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from src.config import Settings
from src.models.audit import SyncConfig
from src.schemas.sync import ConfigFlag, RuntimeConfig

_COLUMNS: dict[ConfigFlag, InstrumentedAttribute[bool]] = {
    "dry_run": SyncConfig.dry_run,
    "auto_sync_enabled": SyncConfig.auto_sync_enabled,
}


class RuntimeConfigStore(Protocol):
    """Storage port for the dashboard run flags; bound to one module."""

    async def get(self) -> RuntimeConfig: ...
    async def toggle(self, flag: ConfigFlag) -> RuntimeConfig: ...


async def resolve_dry_run(config: RuntimeConfigStore | None, fallback: bool) -> bool:
    """Run mode for the next run: the DB flag when a store is wired, else the static flag."""
    return (await config.get()).dry_run if config is not None else fallback


def runtime_defaults(settings: Settings) -> RuntimeConfig:
    """`.env` flags in effect until the dashboard first toggles them into `sync_config`."""
    return RuntimeConfig(dry_run=settings.sync_dry_run, auto_sync_enabled=settings.sync_enabled)


class SqlConfigStore:
    """SQLite implementation; the first toggle seeds the row from the `.env` defaults."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], module: str, defaults: RuntimeConfig
    ) -> None:
        self._sessions = sessions
        self._module = module
        self._defaults = defaults

    async def get(self) -> RuntimeConfig:
        async with self._sessions() as session:
            row = await session.get(SyncConfig, self._module)
        return self._defaults if row is None else RuntimeConfig.model_validate(row)

    async def toggle(self, flag: ConfigFlag) -> RuntimeConfig:
        seed = insert(SyncConfig).values(
            module_name=self._module,
            dry_run=self._defaults.dry_run,
            auto_sync_enabled=self._defaults.auto_sync_enabled,
        )
        column = _COLUMNS[flag]
        flip = (
            update(SyncConfig)
            .where(SyncConfig.module_name == self._module)
            .values({column: not_(column), SyncConfig.updated_at: func.now()})
            .returning(SyncConfig.dry_run, SyncConfig.auto_sync_enabled)
        )
        async with self._sessions.begin() as session:
            await session.execute(seed.on_conflict_do_nothing(index_elements=["module_name"]))
            dry_run, auto_sync = (await session.execute(flip)).one()
        return RuntimeConfig(dry_run=dry_run, auto_sync_enabled=auto_sync)
