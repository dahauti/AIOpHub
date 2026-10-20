"""Frontu FSM API v2 schemas (SPEC section 5.2)."""

from typing import Self

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator


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
    deleted: bool = False


class FrontuCustomField(BaseModel):
    """Workspace custom-field definition from GET /customfields/workspaces."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    type: str | None = None


class FrontuCustomFieldList(BaseModel):
    """Envelope variants: paginated `{"next_page": 0, "items": [...]}` or `{"data": [...]}`.

    Pagination metadata (`next_page`, `page`, ...) is ignored; only the first page is read.
    """

    model_config = ConfigDict(extra="ignore")

    items: list[FrontuCustomField] | None = None
    data: list[FrontuCustomField] | None = None

    @model_validator(mode="after")
    def _require_field_list(self) -> Self:
        if self.items is None and self.data is None:
            raise ValueError("custom fields envelope has neither 'items' nor 'data'")
        return self

    @property
    def fields(self) -> list[FrontuCustomField]:
        """The field list, preferring `items` over `data`."""
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
