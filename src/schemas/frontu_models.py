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
