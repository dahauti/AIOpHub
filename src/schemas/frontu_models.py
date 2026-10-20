"""Frontu FSM API v2 schemas (SPEC section 5.2)."""

from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

# Identifier keys seen in live payloads, in order of precedence.
CUSTOM_FIELD_ID_KEYS = ("guid", "id", "field_id")


class FrontuWorkspaceUpsert(BaseModel):
    """Body for POST /workspaces; upserted by `systemId`, also used for blocked deletes."""

    model_config = ConfigDict(extra="ignore")

    systemId: str
    title: str
    client: str
    type: str = "object"
    address: str | None = None
    city: str | None = None
    zipCode: str | None = None
    serialNumber: str | None = None
    customValues: dict[str, str] | None = None
    warrantyStartDate: str | None = None
    warrantyEndDate: str | None = None
    extendedWarrantyEndDate: str | None = None
    warrantyIndicatorLimit: int | None = None
    deleted: bool = False


class FrontuWorkspaceSummary(BaseModel):
    """Workspace row from GET /workspaces, reduced to the identity-resolution fields."""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    id: str
    systemId: str | None = None
    serialNumber: str | None = None
    type: str | None = None


class FrontuWorkspaceList(BaseModel):
    """Paginated envelope `{"items": [...], "total": n}`; items stay raw for per-row validation."""

    model_config = ConfigDict(extra="ignore")

    items: list[JsonValue]
    total: int | None = None


class FrontuCustomField(BaseModel):
    """Workspace custom-field definition from GET /customfields/workspaces.

    Live payloads carry the identifier as `guid` (preferred), `id` or `field_id`,
    and `type` as an integer code.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    type: int | str | None = None
    notUsedInApp: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _identifier_from_known_keys(cls, data: object) -> object:
        """Use the first present of `CUSTOM_FIELD_ID_KEYS` as `id`."""
        if not isinstance(data, Mapping):
            return data
        for key in CUSTOM_FIELD_ID_KEYS:
            value = data.get(key)
            if value is not None:
                return {**data, "id": value}
        return data


class FrontuCustomFieldList(BaseModel):
    """Envelope variants: paginated `{"next_page": 0, "items": [...]}` or `{"data": [...]}`.

    Pagination metadata (`next_page`, `page`, ...) is ignored; only the first page is read.
    Items stay raw so one corrupt field never rejects the whole listing.
    """

    model_config = ConfigDict(extra="ignore")

    items: list[JsonValue] | None = None
    data: list[JsonValue] | None = None

    @model_validator(mode="after")
    def _require_field_list(self) -> Self:
        if self.items is None and self.data is None:
            raise ValueError("custom fields envelope has neither 'items' nor 'data'")
        return self

    @property
    def raw_fields(self) -> list[JsonValue]:
        """The unvalidated field entries, preferring `items` over `data`."""
        return self.items if self.items is not None else self.data or []


class FrontuCustomFieldIds(BaseModel):
    """Resolved custom-field GUIDs; None when the field does not exist in Frontu."""

    model_config = ConfigDict(frozen=True)

    registration_field_id: str | None = None
    site_field_id: str | None = None

    def build_custom_values(
        self, registration_no: str | None, site_name: str | None
    ) -> dict[str, str] | None:
        """Map values onto resolved GUIDs, omitting unresolved fields and empty values."""
        pairs = ((self.registration_field_id, registration_no), (self.site_field_id, site_name))
        values: dict[str, str] = {}
        for field_id, value in pairs:
            if field_id is not None and value:
                values[field_id] = value
        return values or None


class FrontuUpsertResult(BaseModel):
    """Outcome of POST /workspaces: HTTP status plus the response object."""

    model_config = ConfigDict(frozen=True)

    status_code: int
    body: dict[str, JsonValue]
