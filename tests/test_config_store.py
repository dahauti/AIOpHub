"""Runtime config: `sync_config` store on SQLite, and pipelines preferring it over `.env`."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from src.config import get_settings
from src.database import get_engine, get_sessionmaker, init_db
from src.pipelines.single_vehicle import SingleVehicleSync
from src.pipelines.sync_vehicles import VehicleSyncPipeline
from src.repositories.config_repository import (
    SqlConfigStore,
    resolve_dry_run,
    runtime_defaults,
)
from src.schemas.schedule import ScheduleSettings
from src.schemas.sync import RuntimeConfig
from tests.pipeline_fakes import INTERNAL_CLIENT, NOW, FakeBC, FakeFrontu, vehicle
from tests.repository_fakes import FakeConfigStore, FakeSyncRepository

MODULE = "workspaces_vehicles"
DEFAULTS = RuntimeConfig(dry_run=True, auto_sync_enabled=False)


@pytest_asyncio.fixture
async def store(temp_database: Path) -> AsyncIterator[SqlConfigStore]:
    await init_db()
    yield SqlConfigStore(get_sessionmaker(), MODULE, DEFAULTS)
    await get_engine().dispose()


@pytest.mark.asyncio
async def test_unseeded_module_reads_env_defaults(store: SqlConfigStore) -> None:
    assert await store.get() == DEFAULTS


@pytest.mark.asyncio
async def test_toggle_seeds_row_flips_one_flag_and_persists(store: SqlConfigStore) -> None:
    live = await store.toggle("dry_run")
    auto = await store.toggle("auto_sync_enabled")

    assert live == RuntimeConfig(dry_run=False, auto_sync_enabled=False)
    assert auto == RuntimeConfig(dry_run=False, auto_sync_enabled=True)
    reread = SqlConfigStore(get_sessionmaker(), MODULE, DEFAULTS)
    assert await reread.get() == auto
    assert await store.toggle("dry_run") == RuntimeConfig(dry_run=True, auto_sync_enabled=True)


@pytest.mark.asyncio
async def test_stored_flags_win_over_changed_env_defaults(store: SqlConfigStore) -> None:
    await store.toggle("dry_run")
    env_says_dry = RuntimeConfig(dry_run=True, auto_sync_enabled=True)

    assert await SqlConfigStore(get_sessionmaker(), MODULE, env_says_dry).get() == RuntimeConfig(
        dry_run=False, auto_sync_enabled=False
    )


@pytest.mark.asyncio
async def test_resolve_dry_run_falls_back_to_static_flag_without_store() -> None:
    assert await resolve_dry_run(None, fallback=False) is False
    assert await resolve_dry_run(FakeConfigStore(dry_run=False), fallback=True) is False


@pytest.mark.usefixtures("isolated_env")
def test_runtime_defaults_mirror_env_safety_flags() -> None:
    assert runtime_defaults(get_settings()) == DEFAULTS


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.asyncio
async def test_batch_mode_comes_from_runtime_config_not_static_flag(dry_run: bool) -> None:
    modified = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    bc, frontu = FakeBC([[vehicle("SN-1", modified)]]), FakeFrontu()
    repo = FakeSyncRepository(cursor=modified - timedelta(hours=1))
    pipeline = VehicleSyncPipeline(
        bc,
        frontu,
        repo,
        INTERNAL_CLIENT,
        dry_run=not dry_run,
        config=FakeConfigStore(dry_run=dry_run),
        clock=lambda: NOW,
    )

    result = await pipeline.run()

    assert result is not None and result.dry_run is dry_run
    assert len(frontu.pushed) == (0 if dry_run else 1)
    assert repo.audit[-1].action == ("DRY_RUN_UPSERT" if dry_run else "UPSERT")


@pytest.mark.asyncio
async def test_single_vehicle_mode_comes_from_runtime_config() -> None:
    frontu = FakeFrontu()
    single = SingleVehicleSync(
        FakeBC([[vehicle("SN-1", NOW)]]),
        frontu,
        FakeSyncRepository(),
        INTERNAL_CLIENT,
        lock=asyncio.Lock(),
        dry_run=True,
        config=FakeConfigStore(dry_run=False),
        clock=lambda: NOW,
    )

    result = await single.sync_single_vehicle("SN-1")

    assert (result.outcome, result.dry_run, len(frontu.pushed)) == ("SYNCED", False, 1)


@pytest.mark.asyncio
async def test_update_schedule_seeds_row_persists_and_survives_toggles(
    store: SqlConfigStore,
) -> None:
    weekly = ScheduleSettings(
        schedule_mode="interval", interval_minutes=15, full_sync_enabled=True, full_sync_weekday=2
    )

    saved = await store.update_schedule(weekly)
    toggled = await store.toggle("dry_run")

    assert saved.key == weekly.key and (saved.dry_run, saved.auto_sync_enabled) == (True, False)
    assert toggled.key == weekly.key and toggled.dry_run is False
    reread = await SqlConfigStore(get_sessionmaker(), MODULE, DEFAULTS).get()
    assert reread == toggled


@pytest.mark.asyncio
async def test_update_schedule_keeps_run_flags_of_an_existing_row(store: SqlConfigStore) -> None:
    await store.toggle("auto_sync_enabled")

    saved = await store.update_schedule(ScheduleSettings(nightly_time="23:45"))

    assert (saved.auto_sync_enabled, saved.nightly_time) == (True, "23:45")
