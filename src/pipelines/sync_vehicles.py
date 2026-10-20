"""BC VehicleCard -> Frontu workspace sync pipeline (SPEC section 7)."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from src.adapters.resilience import ExternalServiceError
from src.pipelines.ports import RunState, VehicleSource, WorkspaceSink, utc_now
from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from src.repositories.sync_repository import SyncRepository
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from src.schemas.sync import AuditAction, AuditEntry, ReleaseStatus, SyncRunResult

logger = logging.getLogger(__name__)

MODULE = "workspaces_vehicles"
LOOKBACK = timedelta(minutes=15)
INITIAL_WINDOW = timedelta(days=30)
CONFIRMED_STATUSES = frozenset({200, 201})


class VehicleSyncPipeline:
    """One-module sync run guarded by an in-process lock plus the DB RUNNING claim.

    In dry-run mode Frontu is never mutated, and fingerprints and the cursor are left
    untouched so a later live run still processes every record the dry run saw.
    """

    def __init__(
        self,
        source: VehicleSource,
        sink: WorkspaceSink,
        repository: SyncRepository,
        internal_client_id: str,
        *,
        dry_run: bool = True,
        lock: asyncio.Lock | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._source = source
        self._sink = sink
        self._repo = repository
        self._internal_client_id = internal_client_id
        self._dry_run = dry_run
        self._clock = clock
        self._lock = lock if lock is not None else asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    async def run(self) -> SyncRunResult | None:
        """Run one sync; returns None when another run already holds the module."""
        if self._lock.locked():
            return None
        async with self._lock:
            now = self._clock()
            if not await self._repo.try_acquire(now, now - INITIAL_WINDOW):
                logger.info("Sync skipped: module claimed elsewhere", extra={"module": MODULE})
                return None
            status: ReleaseStatus = "FAILED"
            try:
                result = await self._sync()
                status = "IDLE"
                return result
            finally:
                await self._repo.release(status, self._clock())

    async def _sync(self) -> SyncRunResult:
        cursor = await self._repo.get_cursor()
        run = RunState(field_ids=await self._sink.resolve_custom_fields(), cursor=cursor)
        async for page in self._source.fetch_vehicles(cursor - LOOKBACK):
            if not page:
                continue
            known = await self._repo.get_fingerprints([v.SerialNo for v in page])
            for vehicle in page:
                await self._process(vehicle, known.get(vehicle.SerialNo), run)
            await self._checkpoint(page, run)
        return SyncRunResult(
            processed=run.processed,
            skipped=run.skipped,
            failed=run.failed,
            planned=run.planned,
            cursor=run.cursor,
        )

    async def _process(self, vehicle: BCODataVehicle, known: str | None, run: RunState) -> None:
        payload: FrontuWorkspaceUpsert | None = None
        try:
            ship_to = await self._ship_to(vehicle, run)
            payload = build_workspace(vehicle, ship_to, run.field_ids, self._internal_client_id)
            digest = payload_hash(payload)
            if not vehicle.Blocked and known == digest:
                await self._audit(vehicle, "SKIPPED")
                run.skipped += 1
                return
            if self._dry_run:
                action: AuditAction = "DRY_RUN_ARCHIVE" if vehicle.Blocked else "DRY_RUN_UPSERT"
                await self._audit(vehicle, action, payload=payload)
                run.planned += 1
                return
            result = await self._sink.upsert_workspace(payload)
        except ExternalServiceError as exc:
            await self._fail(vehicle, run, exc.status_code, str(exc), payload)
            return
        if result.status_code not in CONFIRMED_STATUSES:
            detail = f"unexpected Frontu status {result.status_code}"
            await self._fail(vehicle, run, result.status_code, detail, payload)
            return
        if vehicle.Blocked:
            await self._repo.delete_fingerprint(vehicle.SerialNo)
        else:
            await self._repo.save_fingerprint(vehicle.SerialNo, digest, self._clock())
        action = "ARCHIVE" if vehicle.Blocked else "UPSERT"
        await self._audit(vehicle, action, result.status_code, payload)
        run.processed += 1

    async def _ship_to(self, vehicle: BCODataVehicle, run: RunState) -> BCODataShipToAddress | None:
        if not (vehicle.CustomerNo and vehicle.CustomerServiceAddressCode):
            return None
        key = (vehicle.CustomerNo, vehicle.CustomerServiceAddressCode)
        if key not in run.ship_to:
            run.ship_to[key] = await self._source.get_ship_to(*key)
        return run.ship_to[key]

    async def _checkpoint(self, page: list[BCODataVehicle], run: RunState) -> None:
        """Advance to the page maximum, never past the earliest failure; never in dry run."""
        now = self._clock()
        await self._repo.heartbeat(now)
        if self._dry_run:
            return
        candidate = max(v.SystemModifiedAt for v in page)
        if run.earliest_failure is not None:
            candidate = min(candidate, run.earliest_failure)
        if candidate > run.cursor:
            await self._repo.advance_cursor(candidate, now)
            run.cursor = candidate

    async def _fail(
        self,
        vehicle: BCODataVehicle,
        run: RunState,
        status_code: int | None,
        detail: str,
        payload: FrontuWorkspaceUpsert | None,
    ) -> None:
        modified = vehicle.SystemModifiedAt
        if run.earliest_failure is None or modified < run.earliest_failure:
            run.earliest_failure = modified
        run.failed += 1
        logger.warning(
            "Vehicle sync failed: %s",
            detail,
            extra={"run_id": run.run_id, "entity_key": vehicle.SerialNo, "status": status_code},
        )
        await self._audit(vehicle, "FAILED", status_code, payload, detail)

    async def _audit(
        self,
        vehicle: BCODataVehicle,
        action: AuditAction,
        status_code: int | None = None,
        payload: FrontuWorkspaceUpsert | None = None,
        error_message: str | None = None,
    ) -> None:
        body = payload.model_dump(mode="json") if payload is not None else None
        entry = AuditEntry(
            entity_key=vehicle.SerialNo,
            action=action,
            status_code=status_code,
            payload=body,
            error_message=error_message,
        )
        await self._repo.record_audit(entry)
