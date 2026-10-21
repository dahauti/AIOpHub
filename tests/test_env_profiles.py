"""APP_ENV profiles, per-env database routing, shared audit/config and the tasks lock cutoff."""

from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.bc_client import select_connection
from src.config import get_settings
from src.database import get_engine, get_sessionmaker, init_db
from src.pipelines.sync_vehicles import MODULE as VEHICLES_MODULE
from src.repositories.config_repository import SqlConfigStore, runtime_defaults
from src.repositories.sync_repository import SqlSyncRepository
from src.services.task_runner import LOCK_MODULE, TASK_STALE_RUN_AFTER, run_tasks, task_lock
from tests.task_runner_fakes import MockBackends, backends

__all__ = ["backends"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
DEV_URL = "https://dev.frontu.com/client/api/v2"
PROD_URL = "https://acme.frontu.com/client/api/v2"


@pytest.fixture
def profiles(monkeypatch: pytest.MonkeyPatch, isolated_env: None) -> pytest.MonkeyPatch:
    """Both DEV_ and PROD_ overrides set; APP_ENV alone decides which one is active."""
    for key, value in {
        "DEV_BC_COMPANY": "CRONUS DEV",
        "PROD_BC_COMPANY": "ALWARK",
        "DEV_FRONTU_BASE_URL": f"{DEV_URL}/",
        "PROD_FRONTU_BASE_URL": PROD_URL,
        "DEV_FRONTU_API_KEY": "dev-token",
        "PROD_FRONTU_API_KEY": "prod-token",
    }.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_dev_is_default_and_falls_back_to_shared_values(isolated_env: None) -> None:
    settings = get_settings()

    assert settings.app_env == "dev"
    assert settings.active_database_url == "sqlite+aiosqlite:///./data/aio_dev.db"
    assert settings.active_bc_company == "your-company-name"
    assert settings.active_frontu_base_url == ("https://your-subdomain.frontu.com/client/api/v2")
    assert settings.active_frontu_api_key.get_secret_value() == "your-frontu-api-token"


def test_dev_profile_uses_dev_overrides(profiles: pytest.MonkeyPatch) -> None:
    settings = get_settings()

    assert settings.active_bc_company == "CRONUS DEV"
    assert settings.active_frontu_base_url == DEV_URL
    assert settings.active_frontu_api_key.get_secret_value() == "dev-token"
    assert select_connection(settings).company == "CRONUS DEV"


def test_prod_profile_switches_company_frontu_and_database(profiles: pytest.MonkeyPatch) -> None:
    profiles.setenv("APP_ENV", "prod")

    settings = get_settings()

    assert settings.active_database_url == "sqlite+aiosqlite:///./data/aio_prod.db"
    assert settings.active_bc_company == "ALWARK"
    assert settings.active_frontu_base_url == PROD_URL
    assert settings.active_frontu_api_key.get_secret_value() == "prod-token"
    assert "Company('ALWARK')" in select_connection(settings).company_url


def test_prod_without_overrides_uses_saas_company_for_saas_profile(
    monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("BC_ACTIVE_PROFILE", "saas")

    assert get_settings().active_bc_company == "your-company-guid-or-name"


def test_explicit_database_url_overrides_environment_routing(
    monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/sync.db")

    assert get_settings().active_database_url == "sqlite+aiosqlite:///./data/sync.db"


@pytest.mark.parametrize("env", ["staging", "PRODUCTION"])
def test_unknown_app_env_is_rejected(
    monkeypatch: pytest.MonkeyPatch, isolated_env: None, env: str
) -> None:
    monkeypatch.setenv("APP_ENV", env)

    with pytest.raises(ValueError, match="app_env"):
        get_settings()


@pytest.mark.asyncio
@pytest.mark.usefixtures("temp_database")
async def test_tasks_lock_goes_stale_after_15_minutes_but_vehicle_lock_does_not() -> None:
    await init_db()
    vehicles = SqlSyncRepository(get_sessionmaker(), VEHICLES_MODULE)
    started = NOW - TASK_STALE_RUN_AFTER - timedelta(minutes=1)
    try:
        assert await task_lock().try_acquire(started, started)
        assert await vehicles.try_acquire(started, started)

        assert await task_lock().try_acquire(NOW - timedelta(minutes=1), NOW) is False
        assert await task_lock().try_acquire(NOW, NOW) is True
        assert await vehicles.try_acquire(NOW, NOW) is False
    finally:
        await get_engine().dispose()
    assert timedelta(minutes=15) == TASK_STALE_RUN_AFTER


@pytest.mark.asyncio
async def test_task_audit_lands_in_the_shared_dashboard_log(backends: MockBackends) -> None:
    report = await run_tasks(get_settings(), "outbound")

    assert report is not None and report.dry_run is True
    dashboard = SqlSyncRepository(get_sessionmaker(), VEHICLES_MODULE)
    try:
        snapshot = await dashboard.dashboard_snapshot(datetime.now(UTC) - timedelta(hours=1), 20)
    finally:
        await get_engine().dispose()
    actions = [record.action for record in snapshot.recent]
    assert sorted(set(actions)) == ["DRY_RUN_TASK", "TASK_RUN"]
    assert (actions.count("DRY_RUN_TASK"), actions.count("TASK_RUN")) == (4, 1)
    assert snapshot.planned == 0


@pytest.mark.asyncio
async def test_module_toggle_overrides_env_dry_run_for_tasks(backends: MockBackends) -> None:
    settings = get_settings()
    assert settings.sync_dry_run is True
    await init_db()
    store = SqlConfigStore(get_sessionmaker(), LOCK_MODULE, runtime_defaults(settings))
    try:
        assert (await store.toggle("dry_run")).dry_run is False
    finally:
        await get_engine().dispose()

    report = await run_tasks(settings, "outbound")

    assert report is not None and report.dry_run is False
    assert backends.writes
