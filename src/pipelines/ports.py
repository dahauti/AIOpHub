"""Pipeline ports (adapter protocols), clock and per-run mutable state."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle
from src.schemas.frontu_models import (
    FrontuCustomFieldIds,
    FrontuUpsertResult,
    FrontuWorkspaceUpsert,
)


class VehicleSource(Protocol):
    def fetch_vehicles(self, modified_since: datetime) -> AsyncIterator[list[BCODataVehicle]]: ...
    async def get_ship_to(self, customer_no: str, code: str) -> BCODataShipToAddress | None: ...


class WorkspaceSink(Protocol):
    async def resolve_custom_fields(self) -> FrontuCustomFieldIds: ...
    async def upsert_workspace(self, payload: FrontuWorkspaceUpsert) -> FrontuUpsertResult: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunState:
    """Mutable bookkeeping for one pipeline run."""

    field_ids: FrontuCustomFieldIds
    cursor: datetime
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ship_to: dict[tuple[str, str], BCODataShipToAddress | None] = field(default_factory=dict)
    earliest_failure: datetime | None = None
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    planned: int = 0
