"""In-memory fakes for the vehicle pipeline: BC source and Frontu sink (repository re-exported)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from src.adapters.resilience import ExternalServiceError
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle, BCVehicleWarranty
from src.schemas.frontu_models import (
    FrontuCustomFieldIds,
    FrontuUpsertResult,
    FrontuWorkspaceSummary,
    FrontuWorkspaceUpsert,
)
from tests.repository_fakes import FakeSyncRepository as FakeSyncRepository

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
    vin: str | None = None,
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
        VIN=vin,
    )


def warranty(
    serial: str,
    *,
    code: str = "STANDART_FACTORY",
    start: str = "2025-01-15",
    formula: str | None = "1Y",
    run: float = 0,
) -> BCVehicleWarranty:
    return BCVehicleWarranty(
        VehicleSerialNo=serial,
        WarrantyTypeCode=code,
        StartingDate=start,
        TermDateFormula=formula,
        VariableFieldRun1=run,
        Status="Active",
    )


class FakeBC:
    def __init__(
        self,
        pages: list[list[BCODataVehicle]],
        ship_to: dict[tuple[str, str], BCODataShipToAddress] | None = None,
        fail_at_page: int | None = None,
        failing_ship_to: frozenset[tuple[str, str]] = frozenset(),
        failing_vehicles: frozenset[str] = frozenset(),
        warranties: dict[str, list[BCVehicleWarranty]] | None = None,
    ) -> None:
        self.pages = pages
        self.ship_to = ship_to or {}
        self.fail_at_page = fail_at_page
        self.failing_ship_to = failing_ship_to
        self.failing_vehicles = failing_vehicles
        self.warranties = warranties or {}
        self.failing_warranties: frozenset[str] = frozenset()
        self.vehicle_calls: list[str] = []
        self.since: list[datetime] = []
        self.ship_to_calls: list[tuple[str, str]] = []
        self.warranty_calls: list[str] = []

    async def fetch_vehicles(self, modified_since: datetime) -> AsyncIterator[list[BCODataVehicle]]:
        self.since.append(modified_since)
        for index, page in enumerate(self.pages):
            await asyncio.sleep(0)
            if index == self.fail_at_page:
                raise ExternalServiceError("business-central", 503, "unavailable")
            yield page

    async def get_vehicle(self, serial_no: str) -> BCODataVehicle | None:
        self.vehicle_calls.append(serial_no)
        await asyncio.sleep(0)
        if serial_no in self.failing_vehicles:
            raise ExternalServiceError("business-central", 503, "unavailable")
        matches = [v for page in self.pages for v in page if v.SerialNo == serial_no]
        return matches[0] if matches else None

    async def get_ship_to(self, customer_no: str, code: str) -> BCODataShipToAddress | None:
        self.ship_to_calls.append((customer_no, code))
        if (customer_no, code) in self.failing_ship_to:
            raise ExternalServiceError("business-central", 500, "ship-to lookup failed")
        return self.ship_to.get((customer_no, code))

    async def get_vehicle_warranties(self, serial_no: str) -> list[BCVehicleWarranty]:
        self.warranty_calls.append(serial_no)
        if serial_no in self.failing_warranties:
            raise ExternalServiceError("business-central", 503, "warranty lookup failed")
        return self.warranties.get(serial_no, [])


def workspace(
    workspace_id: str,
    serial_number: str | None,
    *,
    system_id: str | None = None,
    kind: str = "object",
) -> FrontuWorkspaceSummary:
    return FrontuWorkspaceSummary(
        id=workspace_id, systemId=system_id, serialNumber=serial_number, type=kind
    )


class FakeFrontu:
    def __init__(
        self,
        status_code: int = 201,
        failures: dict[str, int] | None = None,
    ) -> None:
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
