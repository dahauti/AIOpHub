"""In-memory fakes for the vehicle pipeline: BC source, Frontu sink and sync repository."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from src.adapters.resilience import ExternalServiceError
from src.repositories.sync_statements import STALE_RUN_AFTER
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle
from src.schemas.frontu_models import (
    FrontuCustomFieldIds,
    FrontuUpsertResult,
    FrontuWorkspaceUpsert,
)
from src.schemas.sync import RUNNING, AuditEntry, DashboardSnapshot, ReleaseStatus

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
INTERNAL_CLIENT = "INTERNAL-CO"
REG_ID = "reg-guid"
SITE_ID = "site-guid"
FIELD_IDS = FrontuCustomFieldIds(registration_field_id=REG_ID, site_field_id=SITE_ID)


def vehicle(
    serial: str,
    modified: datetime,
    *,
    blocked: bool = False,
    customer: str | None = None,
    address_code: str | None = None,
    registration: str | None = None,
    make: str | None = None,
    model: str | None = None,
) -> BCODataVehicle:
    return BCODataVehicle(
        SerialNo=serial,
        SystemModifiedAt=modified,
        Blocked=blocked,
        CustomerNo=customer,
        CustomerServiceAddressCode=address_code,
        RegistrationNo=registration,
        MakeCode=make,
        ModelCode=model,
    )


class FakeBC:
    def __init__(
        self,
        pages: list[list[BCODataVehicle]],
        ship_to: dict[tuple[str, str], BCODataShipToAddress] | None = None,
        fail_at_page: int | None = None,
        failing_ship_to: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self.pages = pages
        self.ship_to = ship_to or {}
        self.fail_at_page = fail_at_page
        self.failing_ship_to = failing_ship_to
        self.since: list[datetime] = []
        self.ship_to_calls: list[tuple[str, str]] = []

    async def fetch_vehicles(self, modified_since: datetime) -> AsyncIterator[list[BCODataVehicle]]:
        self.since.append(modified_since)
        for index, page in enumerate(self.pages):
            await asyncio.sleep(0)
            if index == self.fail_at_page:
                raise ExternalServiceError("business-central", 503, "unavailable")
            yield page

    async def get_ship_to(self, customer_no: str, code: str) -> BCODataShipToAddress | None:
        self.ship_to_calls.append((customer_no, code))
        if (customer_no, code) in self.failing_ship_to:
            raise ExternalServiceError("business-central", 500, "ship-to lookup failed")
        return self.ship_to.get((customer_no, code))


class FakeFrontu:
    def __init__(self, status_code: int = 201, failures: dict[str, int] | None = None) -> None:
        self.status_code = status_code
        self.failures = failures or {}
        self.pushed: list[FrontuWorkspaceUpsert] = []

    async def resolve_custom_fields(self) -> FrontuCustomFieldIds:
        return FIELD_IDS

    async def upsert_workspace(self, payload: FrontuWorkspaceUpsert) -> FrontuUpsertResult:
        self.pushed.append(payload)
        if payload.systemId in self.failures:
            raise ExternalServiceError("frontu", self.failures[payload.systemId], "rejected")
        return FrontuUpsertResult(status_code=self.status_code, body={"id": payload.systemId})


class FakeSyncRepository:
    """Mirrors the SQL repository semantics (atomic claim, forward-only cursor)."""

    def __init__(
        self,
        cursor: datetime | None = None,
        status: str = "IDLE",
        updated_at: datetime | None = None,
    ) -> None:
        self.cursor = cursor
        self.status = status
        self.updated_at = updated_at
        self.fingerprints: dict[str, str] = {}
        self.audit: list[AuditEntry] = []
        self.statuses: list[str] = []
        self.heartbeats = 0

    async def try_acquire(self, now: datetime, initial_cursor: datetime) -> bool:
        if self.cursor is None:
            self.cursor = initial_cursor
        elif (
            self.status == RUNNING
            and self.updated_at is not None
            and self.updated_at >= now - STALE_RUN_AFTER
        ):
            return False
        self.status, self.updated_at = RUNNING, now
        self.statuses.append(RUNNING)
        return True

    async def heartbeat(self, now: datetime) -> None:
        self.heartbeats += 1
        self.updated_at = now

    async def release(self, status: ReleaseStatus, now: datetime) -> None:
        self.status, self.updated_at = status, now
        self.statuses.append(status)

    async def get_cursor(self) -> datetime:
        assert self.cursor is not None
        return self.cursor

    async def advance_cursor(self, cursor: datetime, now: datetime) -> None:
        if self.cursor is None or cursor > self.cursor:
            self.cursor = cursor

    async def get_fingerprints(self, keys: Sequence[str]) -> dict[str, str]:
        return {key: self.fingerprints[key] for key in keys if key in self.fingerprints}

    async def save_fingerprint(self, key: str, payload_hash: str, now: datetime) -> None:
        self.fingerprints[key] = payload_hash

    async def delete_fingerprint(self, key: str) -> None:
        self.fingerprints.pop(key, None)

    async def record_audit(self, entry: AuditEntry) -> None:
        self.audit.append(entry)

    async def dashboard_snapshot(self, since: datetime, limit: int) -> DashboardSnapshot:
        return DashboardSnapshot(status=self.status, cursor_timestamp=self.cursor)
