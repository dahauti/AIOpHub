"""Business Central OData V4 raw schemas (SPEC section 5.1)."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class BCODataVehicle(BaseModel):
    """Row from the BC `VehicleCard` OData page."""

    model_config = ConfigDict(extra="ignore")

    SerialNo: str = Field(min_length=1)
    VIN: str | None = None
    MakeCode: str | None = None
    ModelCode: str | None = None
    RegistrationNo: str | None = None
    CustomerNo: str | None = None
    CustomerServiceAddressCode: str | None = None
    Blocked: bool = False
    SystemModifiedAt: datetime
    LastDateModified: date | None = None


class BCODataShipToAddress(BaseModel):
    """Row from the BC `TG_ShipToAdress` OData page."""

    model_config = ConfigDict(extra="ignore")

    Customer_No: str
    Code: str
    Name: str | None = None
    Address: str | None = None
    City: str | None = None
    Post_Code: str | None = None


class BCVehicleWarranty(BaseModel):
    """Row from the BC `VehicleWarrList` OData page (SPEC section 9.1)."""

    model_config = ConfigDict(extra="ignore")

    VehicleSerialNo: str
    WarrantyTypeCode: str
    StartingDate: str
    TermDateFormula: str | None = None
    VariableFieldRun1: int | float = 0
    Status: str


class ODataCollection(BaseModel):
    """OData V4 collection envelope: `{"@odata.context": ..., "value": [...]}`."""

    model_config = ConfigDict(extra="ignore")

    value: list[dict[str, JsonValue]]
