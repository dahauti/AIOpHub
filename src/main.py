"""FastAPI entrypoint: composition root, background sync daemon and dashboard routes."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from src.adapters.bc_client import BCClient, BCConnection, onprem_connection, saas_connection
from src.adapters.frontu_client import FrontuClient
from src.api.routes_ui import router
from src.config import Settings, get_settings
from src.database import get_engine, get_sessionmaker
from src.pipelines.scheduler import SyncRunner
from src.pipelines.sync_vehicles import MODULE, VehicleSyncPipeline
from src.repositories.sync_repository import SqlSyncRepository

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 30.0


def select_connection(settings: Settings) -> BCConnection:
    """BC endpoint for the configured BC_ACTIVE_PROFILE."""
    if settings.bc_active_profile == "saas":
        return saas_connection(settings)
    return onprem_connection(settings)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire adapters, start the periodic sync daemon, and tear everything down on exit."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    repository = SqlSyncRepository(get_sessionmaker(), MODULE)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
        pipeline = VehicleSyncPipeline(
            BCClient(http, select_connection(settings)),
            FrontuClient(http, settings.frontu_subdomain, settings.frontu_api_key),
            repository,
            settings.internal_company_client_id,
            dry_run=settings.sync_dry_run,
        )
        if settings.sync_dry_run:
            logger.warning("SYNC_DRY_RUN is on: Frontu will not be modified")
        runner = SyncRunner(pipeline, repository)
        app.state.sync_control = runner
        daemon = runner.start_daemon(
            enabled=settings.sync_enabled, interval_seconds=settings.sync_interval_seconds
        )
        try:
            yield
        finally:
            if daemon is not None:
                daemon.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await daemon
            await runner.shutdown()
    await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="AIOpHub Sync Engine", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
