"""Runtime dashboard toggles in `sync_config`; a module without a row uses the `.env` defaults."""

from typing import Protocol

from sqlalchemy import func, not_, update
from sqlalchemy.dialects.sqlite import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from src.config import Settings
from src.models.audit import SyncConfig
from src.schemas.schedule import ScheduleSettings
from src.schemas.sync import ConfigFlag, RuntimeConfig

_COLUMNS: dict[ConfigFlag, InstrumentedAttribute[bool]] = {
    "dry_run": SyncConfig.dry_run,
    "auto_sync_enabled": SyncConfig.auto_sync_enabled,
}
MODULE_PK = "module_name"
SCHEDULE_FIELDS = set(ScheduleSettings.model_fields)


class RuntimeConfigStore(Protocol):
    """Storage port for the dashboard run flags and schedule; bound to one module."""

    async def get(self) -> RuntimeConfig: ...
    async def toggle(self, flag: ConfigFlag) -> RuntimeConfig: ...
    async def update_schedule(self, schedule: ScheduleSettings) -> RuntimeConfig: ...


async def resolve_dry_run(config: RuntimeConfigStore | None, fallback: bool) -> bool:
    """Run mode for the next run: the DB flag when a store is wired, else the static flag."""
    return (await config.get()).dry_run if config is not None else fallback


def runtime_defaults(settings: Settings) -> RuntimeConfig:
    """`.env` flags in effect until the dashboard first toggles them into `sync_config`."""
    return RuntimeConfig(dry_run=settings.sync_dry_run, auto_sync_enabled=settings.sync_enabled)


class SqlConfigStore:
    """SQLite implementation; the first write seeds the row from the `.env` defaults."""

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
        column = _COLUMNS[flag]
        flip = (
            update(SyncConfig)
            .where(SyncConfig.module_name == self._module)
            .values({column: not_(column), SyncConfig.updated_at: func.now()})
            .returning(SyncConfig)
        )
        async with self._sessions.begin() as session:
            await session.execute(self._seed().on_conflict_do_nothing(index_elements=[MODULE_PK]))
            row = (await session.execute(flip)).scalar_one()
            return RuntimeConfig.model_validate(row)

    async def update_schedule(self, schedule: ScheduleSettings) -> RuntimeConfig:
        """Replace the schedule columns; the run-mode flags are left as they are."""
        values = schedule.model_dump(include=SCHEDULE_FIELDS)
        upsert = (
            self._seed()
            .values(values)
            .on_conflict_do_update(
                index_elements=[MODULE_PK], set_={**values, "updated_at": func.now()}
            )
            .returning(SyncConfig)
        )
        async with self._sessions.begin() as session:
            row = (await session.execute(upsert)).scalar_one()
            return RuntimeConfig.model_validate(row)

    def _seed(self) -> Insert:
        return insert(SyncConfig).values(module_name=self._module, **self._defaults.model_dump())
