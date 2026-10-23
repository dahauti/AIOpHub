"""Frontu FSM API v2 schemas for Tasks, Materials and Works (SPEC_TASKS section 1)."""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

# Identifier keys accepted when a reference arrives as a nested object, in order of precedence.
REFERENCE_ID_KEYS = ("guid", "id", "systemId")
_MINUTES_PER_HOUR = Decimal(60)


def _reference_id(value: object) -> object:
    """Reduce a nested `{guid|id|systemId: ...}` reference object to its identifier."""
    if not isinstance(value, Mapping):
        return value
    for key in REFERENCE_ID_KEYS:
        candidate = value.get(key)
        if candidate is not None and str(candidate).strip():
            return candidate
    return None


def hours_to_duration_minutes(hours: Decimal | float) -> int:
    """Frontu work `duration`: hours * 60, rounded half-up to whole minutes."""
    minutes = Decimal(str(hours)) * _MINUTES_PER_HOUR
    return int(minutes.quantize(Decimal(1), rounding=ROUND_HALF_UP))


class FrontuTask(BaseModel):
    """Task from Frontu `/tasks`; `systemId` carries the BC Service Order No when linked."""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    guid: str
    code: str | None = None
    systemId: str | None = None
    title: str | None = None
    description: str | None = None
    plannedTime: str | None = None
    status: int | None = None
    statusLiteral: str | None = None
    client: str | None = None
    workspace: str | None = None
    taskType: str | None = None
    user: str | None = None
    version: int | None = None

    @field_validator("client", "workspace", "taskType", "user", mode="before")
    @classmethod
    def _flatten_reference(cls, value: object) -> object:
        return _reference_id(value)


class FrontuTaskPayload(BaseModel):
    """Body for POST /tasks; `guid` set updates an existing task, otherwise one is created."""

    model_config = ConfigDict(extra="ignore")

    guid: str | None = None
    systemId: str | None = None
    title: str
    description: str | None = None
    plannedTime: str | None = None
    client: str
    workspace: str | None = None
    taskType: str | None = None
    user: str | None = None


class FrontuTaskList(BaseModel):
    """Paged envelope `{"items": [...], "next_page": n}`; `next_page == 0` ends the listing."""

    model_config = ConfigDict(extra="ignore")

    items: list[JsonValue]
    next_page: int = 0


class FrontuEntityRef(BaseModel):
    """Identifier of an entity returned by a Frontu create call."""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    guid: str = Field(min_length=1)


class FrontuCatalogEntry(FrontuEntityRef):
    """Row of `/clients` or `/workspaces?systemId=`, or the `/task_types/details` object.

    Shapes: contracts/frontu/clients_2216.json, workspaces_30971.json,
    task_types_details_SERVICE.json.
    """

    systemId: str | None = None


class FrontuNotFound(BaseModel):
    """404 body of `/task_types/details` (contracts/frontu/task_types_details_not_found.json)."""

    model_config = ConfigDict(extra="ignore")

    error: str


class _FrontuTaskEntity(BaseModel):
    """Fields shared by materials and works registered on a task."""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    guid: str | None = None
    systemId: str
    task: str
    workspace: str | None = None
    user: str | None = None

    @field_validator("task", "workspace", "user", mode="before")
    @classmethod
    def _flatten_reference(cls, value: object) -> object:
        return _reference_id(value)


class FrontuMaterial(_FrontuTaskEntity):
    """Material on a Frontu task; `standartizedMaterial` is the catalog GUID when mapped."""

    amount: float
    standartizedMaterial: str | None = None


class FrontuWork(_FrontuTaskEntity):
    """Labor on a Frontu task; `duration` is whole minutes (see `hours_to_duration_minutes`)."""

    duration: int
    standartizedWork: str | None = None
