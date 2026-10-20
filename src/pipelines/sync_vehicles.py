"""BC VehicleCard -> Frontu workspace sync pipeline (SPEC section 7)."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from src.adapters.resilience import ExternalServiceError
from src.pipelines.audit_helpers import (
    record_audit,
    record_failure,
    write_action,
    write_workspace,
)
from src.pipelines.identity_resolver import IdentityDecision, LegacyIndex
from src.pipelines.ports import RunState, VehicleSource, WorkspaceSink, utc_now
from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from src.pipelines.warranty_resolver import resolve_warranty
from src.repositories.sync_repository import SyncRepository
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from src.schemas.sync import ReleaseStatus, SyncRunResult
from src.schemas.warranty import ResolvedWarranty

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
            warranty = await self._warranty(vehicle, run)
            payload = build_workspace(
                vehicle, ship_to, run.field_ids, self._internal_client_id, warranty
            )
            digest = payload_hash(payload)
            if not vehicle.Blocked and known == digest:
                await record_audit(self._repo, vehicle.SerialNo, "SKIPPED")
                run.skipped += 1
                return
            decision = await self._identity(vehicle, run)
            if decision.kind == "AMBIGUOUS":
                # Needs a human fix in Frontu, so a retry cannot help: let the cursor advance.
                await record_failure(
                    self._repo,
                    run,
                    vehicle,
                    None,
                    decision.ambiguity_detail,
                    payload,
                    "FAILED_AMBIGUOUS_MATCH",
                    hold_cursor=False,
                )
                return
            action = write_action(vehicle, decision, single=False, dry_run=self._dry_run)
            if self._dry_run:
                await record_audit(self._repo, vehicle.SerialNo, action, payload=payload)
                run.planned += 1
                return
            result = await write_workspace(self._sink, decision, payload)
        except ExternalServiceError as exc:
            await record_failure(self._repo, run, vehicle, exc.status_code, str(exc), payload)
            return
        if result.status_code not in CONFIRMED_STATUSES:
            detail = f"unexpected Frontu status {result.status_code}"
            await record_failure(self._repo, run, vehicle, result.status_code, detail, payload)
            return
        if vehicle.Blocked:
            await self._repo.delete_fingerprint(vehicle.SerialNo)
        else:
            await self._repo.save_fingerprint(vehicle.SerialNo, digest, self._clock())
        await record_audit(self._repo, vehicle.SerialNo, action, result.status_code, payload)
        run.processed += 1

    async def _identity(self, vehicle: BCODataVehicle, run: RunState) -> IdentityDecision:
        """Resolve against a Frontu index loaded lazily, at most once per run."""
        if run.identity is None:
            run.identity = LegacyIndex(await self._sink.list_workspaces())
        return run.identity.resolve(vehicle)

    async def _ship_to(self, vehicle: BCODataVehicle, run: RunState) -> BCODataShipToAddress | None:
        if not (vehicle.CustomerNo and vehicle.CustomerServiceAddressCode):
            return None
        key = (vehicle.CustomerNo, vehicle.CustomerServiceAddressCode)
        if key not in run.ship_to:
            run.ship_to[key] = await self._source.get_ship_to(*key)
        return run.ship_to[key]

    async def _warranty(self, vehicle: BCODataVehicle, run: RunState) -> ResolvedWarranty:
        if vehicle.SerialNo not in run.warranties:
            run.warranties[vehicle.SerialNo] = await self._source.get_vehicle_warranties(
                vehicle.SerialNo
            )
        return resolve_warranty(run.warranties[vehicle.SerialNo])

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
