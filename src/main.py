"""FastAPI entrypoint: composition root, background sync daemon and dashboard routes."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.adapters.bc_client import BCClient, select_connection
from src.adapters.frontu_client import FrontuClient
from src.api.config_handlers import config_router
from src.api.routes_ui import router
from src.api.schedule_handlers import schedule_router
from src.config import get_settings
from src.database import get_engine, get_sessionmaker, init_db
from src.logging_setup import configure_logging
from src.pipelines.scheduler import SyncRunner
from src.pipelines.single_vehicle import SingleVehicleSync
from src.pipelines.sync_vehicles import MODULE, VehicleSyncPipeline
from src.repositories.config_repository import SqlConfigStore, runtime_defaults
from src.repositories.sync_repository import SqlSyncRepository

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

HTTP_TIMEOUT_SECONDS = 30.0


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire adapters, start the periodic sync daemon, and tear everything down on exit."""
    settings = get_settings()
    log_file = configure_logging(settings.log_level)
    logger.info("Logging to %s", log_file)
    await init_db()  # creates tables added since deployment (e.g. sync_config)
    repository = SqlSyncRepository(get_sessionmaker(), MODULE)
    config = SqlConfigStore(get_sessionmaker(), MODULE, runtime_defaults(settings))
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
        bc = BCClient(http, select_connection(settings))
        frontu = FrontuClient(http, settings.frontu_subdomain, settings.frontu_api_key)
        client_id, run_lock = settings.internal_company_client_id, asyncio.Lock()
        lookback = timedelta(days=settings.sync_initial_lookback_days)
        pipeline = VehicleSyncPipeline(
            bc, frontu, repository, client_id, config=config, initial_lookback=lookback,
            lock=run_lock,
        )
        app.state.single_sync = SingleVehicleSync(
            bc, frontu, repository, client_id, lock=run_lock, config=config
        )
        await _log_runtime_config(config)
        runner = SyncRunner(pipeline, repository, config)
        app.state.sync_control = runner
        daemon = runner.start_daemon(settings.sync_interval_seconds)  # schedule check tick
        try:
            yield
        finally:
            daemon.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await daemon
            await runner.shutdown()
    await get_engine().dispose()


async def _log_runtime_config(config: SqlConfigStore) -> None:
    current = await config.get()
    if current.dry_run:
        logger.warning("Dry run is on: Frontu will not be modified")
    if not current.auto_sync_enabled:
        logger.warning("Auto-sync is off: scheduled runs are skipped until enabled")


def create_app() -> FastAPI:
    app = FastAPI(title="AIOpHub Sync Engine", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    app.include_router(config_router)
    app.include_router(schedule_router)
    return app


app = create_app()
