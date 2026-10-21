"""Business Central OData V4 schemas for Service Orders and TG_Lines (SPEC_TASKS section 1)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# BC line `Type` value for a comment (non-accounting) line.
COMMENT_LINE_TYPE = " "

# Edm.Decimal goes over the wire as a JSON number, not pydantic's default string.
ODataDecimal = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class BCServiceOrderHeader(BaseModel):
    """Row from the BC `ServiceOrders` OData page; `No` is None until BC assigns it on create."""

    model_config = ConfigDict(extra="ignore")

    No: str | None = None
    SelltoCustomerNo: str | None = None
    VehicleSerialNo: str | None = None
    DealType: str | None = None
    DocumentStatus: str | None = None
    Tasker_ID: str | None = None
    Tasker_Document_No: str | None = None
    Last_Tasker_Sinhr_Time: datetime | None = None
    PlannedServiceDate: date | None = None
    OrderTime: str | None = None
    Description: str | None = None
    WorkDescription: str | None = None
    SystemModifiedAt: datetime | None = None


class BCServiceOrderLine(BaseModel):
    """Row from the BC `TG_Lines` OData page."""

    model_config = ConfigDict(extra="ignore")

    DocumentNo: str = Field(min_length=1)
    LineNo: int
    Type: str = COMMENT_LINE_TYPE
    No: str | None = None
    Description: str | None = None
    Description_2: str | None = None
    Quantity: ODataDecimal = Decimal(0)
    UnitofMeasureCode: str | None = None
    UnitPrice: ODataDecimal = Decimal(0)
    LineAmount: ODataDecimal = Decimal(0)
    LocationCode: str | None = None
    Service_Order_Job: str | None = None
    Frontu_Integration_Line_GUID: str | None = None
