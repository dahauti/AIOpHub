"""Targeted single-vehicle sync for canary/dev testing; never touches `sync_states`.

Unlike the batch run it always pushes in live mode (no fingerprint skip), and it shares
the batch pipeline's in-process lock so the two never mutate the same records at once.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from src.adapters.resilience import ExternalServiceError
from src.pipelines.audit_helpers import record_audit, write_action, write_workspace
from src.pipelines.identity_resolver import LegacyIndex
from src.pipelines.ports import WorkspaceSink, utc_now
from src.pipelines.sync_vehicles import CONFIRMED_STATUSES
from src.pipelines.vehicle_mapping import build_workspace, payload_hash
from src.pipelines.warranty_resolver import resolve_warranty
from src.repositories.sync_repository import SyncRepository
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle, BCVehicleWarranty
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from src.schemas.sync import AuditAction, SingleSyncDebug, SingleSyncResult

logger = logging.getLogger(__name__)

# Reuse a recent Frontu listing so repeated canary clicks skip the full index scan.
SINGLE_INDEX_MAX_AGE = 300.0


class SingleVehicleSource(Protocol):
    async def get_vehicle(self, serial_no: str) -> BCODataVehicle | None: ...
    async def get_ship_to(self, customer_no: str, code: str) -> BCODataShipToAddress | None: ...
    async def get_vehicle_warranties(self, serial_no: str) -> list[BCVehicleWarranty]: ...


@runtime_checkable
class SingleVehicleControl(Protocol):
    """Port consumed by the dashboard's single-vehicle route."""

    async def sync_single_vehicle(
        self, serial_no: str, force_live: bool = False
    ) -> SingleSyncResult: ...


class SingleVehicleSync:
    def __init__(
        self,
        source: SingleVehicleSource,
        sink: WorkspaceSink,
        repository: SyncRepository,
        internal_client_id: str,
        *,
        lock: asyncio.Lock,
        dry_run: bool = True,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._source = source
        self._sink = sink
        self._repo = repository
        self._internal_client_id = internal_client_id
        self._lock = lock
        self._dry_run = dry_run
        self._clock = clock

    async def sync_single_vehicle(
        self, serial_no: str, force_live: bool = False
    ) -> SingleSyncResult:
        """Sync one vehicle; live when `force_live` or SYNC_DRY_RUN is off, else dry run."""
        live = force_live or not self._dry_run
        if self._lock.locked():
            return SingleSyncResult(serial_no=serial_no, outcome="BUSY", dry_run=not live)
        async with self._lock:
            return await self._sync(serial_no, live)

    async def _sync(self, serial_no: str, live: bool) -> SingleSyncResult:
        payload: FrontuWorkspaceUpsert | None = None
        debug: SingleSyncDebug | None = None
        try:
            vehicle = await self._source.get_vehicle(serial_no)
            if vehicle is None:
                logger.info("Single sync: vehicle %r not found in BC", serial_no)
                return SingleSyncResult(serial_no=serial_no, outcome="NOT_FOUND", dry_run=not live)
            ship_to = await self._ship_to(vehicle)
            warranties = await self._source.get_vehicle_warranties(vehicle.SerialNo)
            warranty = resolve_warranty(warranties)
            field_ids = await self._sink.resolve_custom_fields()
            payload = build_workspace(
                vehicle, ship_to, field_ids, self._internal_client_id, warranty
            )
            debug = SingleSyncDebug(warranties=warranties, warranty=warranty, payload=payload)
            listing = await self._sink.list_workspaces(max_age=SINGLE_INDEX_MAX_AGE)
            decision = LegacyIndex(listing).resolve(vehicle)
            if decision.kind == "AMBIGUOUS":
                ambiguous: AuditAction = "FAILED_AMBIGUOUS_MATCH"
                detail = decision.ambiguity_detail
                return await self._fail(serial_no, None, detail, payload, debug, ambiguous, live)
            action = write_action(vehicle, decision, single=True, dry_run=not live)
            if not live:
                await record_audit(self._repo, serial_no, action, payload=payload)
                return SingleSyncResult(
                    serial_no=serial_no,
                    outcome="DRY_RUN",
                    action=action,
                    dry_run=True,
                    debug=debug,
                )
            result = await write_workspace(self._sink, decision, payload)
        except ExternalServiceError as exc:
            return await self._fail(serial_no, exc.status_code, str(exc), payload, debug)
        if result.status_code not in CONFIRMED_STATUSES:
            detail = f"unexpected Frontu status {result.status_code}"
            return await self._fail(serial_no, result.status_code, detail, payload, debug)
        if vehicle.Blocked:
            await self._repo.delete_fingerprint(serial_no)
        else:
            await self._repo.save_fingerprint(serial_no, payload_hash(payload), self._clock())
        await record_audit(self._repo, serial_no, action, result.status_code, payload)
        return SingleSyncResult(
            serial_no=serial_no,
            outcome="SYNCED",
            action=action,
            status_code=result.status_code,
            dry_run=False,
            debug=debug,
        )

    async def _ship_to(self, vehicle: BCODataVehicle) -> BCODataShipToAddress | None:
        if vehicle.CustomerNo and vehicle.CustomerServiceAddressCode:
            return await self._source.get_ship_to(
                vehicle.CustomerNo, vehicle.CustomerServiceAddressCode
            )
        return None

    async def _fail(
        self,
        serial_no: str,
        status_code: int | None,
        detail: str,
        payload: FrontuWorkspaceUpsert | None,
        debug: SingleSyncDebug | None,
        action: AuditAction = "FAILED",
        live: bool = True,
    ) -> SingleSyncResult:
        logger.warning(
            "Single vehicle sync failed: %s",
            detail,
            extra={"entity_key": serial_no, "status": status_code},
        )
        await record_audit(self._repo, serial_no, action, status_code, payload, detail)
        return SingleSyncResult(
            serial_no=serial_no,
            outcome="FAILED",
            action=None if action == "FAILED" else action,
            status_code=status_code,
            error=detail,
            dry_run=not live,
            debug=debug,
        )
