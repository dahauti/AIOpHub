"""Frontu FSM API v2 schemas (SPEC section 5.2)."""

from pydantic import BaseModel, ConfigDict


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
    """`{"data": [...]}` envelope variant of the custom-field listing."""

    model_config = ConfigDict(extra="ignore")

    data: list[FrontuCustomField]


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
