"""Domain schema contracts from SPEC section 5, validated against mocked vendor payloads."""

from datetime import date, timedelta

import pytest
from pydantic import JsonValue, ValidationError

from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle
from src.schemas.frontu_models import FrontuWorkspaceUpsert

BC_VEHICLE_PAYLOAD: dict[str, JsonValue] = {
    "@odata.etag": 'W/"JzQ0O0VnQUFBQUo3LzFNQU4="',
    "SerialNo": "SN-0001",
    "VIN": "YV2RT40A0KB123456",
    "MakeCode": "VOLVO",
    "ModelCode": "FH16",
    "RegistrationNo": "ABC123",
    "CustomerNo": "C00010",
    "CustomerServiceAddressCode": "VILNIUS",
    "Blocked": False,
    "SystemModifiedAt": "2026-10-19T08:15:30.123Z",
    "LastDateModified": "2026-10-19",
    "UndocumentedVendorField": 42,
}

BC_SHIP_TO_PAYLOAD: dict[str, JsonValue] = {
    "@odata.etag": 'W/"JzE5OzEyMzQ1Njc4OTAxMjM0NTY3OTAx"',
    "Customer_No": "C00010",
    "Code": "VILNIUS",
    "Name": "UAB Klientas Vilnius",
    "Address": "Gedimino pr. 1",
    "City": "Vilnius",
    "Post_Code": "LT-01103",
    "Country_Region_Code": "LT",
}


def test_bc_vehicle_parses_odata_payload_and_ignores_extras() -> None:
    vehicle = BCODataVehicle.model_validate(BC_VEHICLE_PAYLOAD)

    assert vehicle.SerialNo == "SN-0001"
    assert vehicle.CustomerServiceAddressCode == "VILNIUS"
    assert vehicle.SystemModifiedAt.utcoffset() == timedelta(0)
    assert vehicle.LastDateModified == date(2026, 10, 19)
    assert "UndocumentedVendorField" not in vehicle.model_dump()
    assert vehicle.model_extra is None


def test_bc_vehicle_minimal_payload_applies_defaults() -> None:
    vehicle = BCODataVehicle.model_validate(
        {"SerialNo": "SN-0002", "SystemModifiedAt": "2026-10-19T08:15:30Z"}
    )

    assert vehicle.Blocked is False
    assert vehicle.VIN is None
    assert vehicle.CustomerNo is None
    assert vehicle.LastDateModified is None


@pytest.mark.parametrize(
    "payload",
    [
        {"SystemModifiedAt": "2026-10-19T08:15:30Z"},
        {"SerialNo": "", "SystemModifiedAt": "2026-10-19T08:15:30Z"},
        {"SerialNo": "SN-0003"},
    ],
    ids=["missing-serial", "empty-serial", "missing-modified-at"],
)
def test_bc_vehicle_rejects_invalid_required_fields(payload: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        BCODataVehicle.model_validate(payload)


def test_bc_ship_to_parses_odata_payload_and_ignores_extras() -> None:
    ship_to = BCODataShipToAddress.model_validate(BC_SHIP_TO_PAYLOAD)

    assert (ship_to.Customer_No, ship_to.Code) == ("C00010", "VILNIUS")
    assert ship_to.Post_Code == "LT-01103"
    assert "Country_Region_Code" not in ship_to.model_dump()


@pytest.mark.parametrize("missing", ["Customer_No", "Code"])
def test_bc_ship_to_requires_keys(missing: str) -> None:
    payload = {k: v for k, v in BC_SHIP_TO_PAYLOAD.items() if k != missing}

    with pytest.raises(ValidationError):
        BCODataShipToAddress.model_validate(payload)


def test_frontu_workspace_defaults() -> None:
    workspace = FrontuWorkspaceUpsert(systemId="SN-0001", title="VOLVO FH16", client="C00010")

    assert workspace.type == "object"
    assert workspace.deleted is False
    assert workspace.address is None
    assert workspace.serialNumber is None
    assert workspace.customValues is None


def test_frontu_workspace_full_and_blocked_payload() -> None:
    workspace = FrontuWorkspaceUpsert(
        systemId="SN-0001",
        title="VOLVO FH16",
        client="C00010",
        address="Gedimino pr. 1",
        city="Vilnius",
        zipCode="LT-01103",
        serialNumber="YV2RT40A0KB123456",
        customValues={"6f1c2a9e-0000-4000-8000-000000000001": "ABC123"},
        deleted=True,
    )

    dumped = workspace.model_dump()
    assert dumped["deleted"] is True
    assert dumped["customValues"] == {"6f1c2a9e-0000-4000-8000-000000000001": "ABC123"}


def test_frontu_workspace_ignores_extras_and_requires_system_id() -> None:
    parsed = FrontuWorkspaceUpsert.model_validate(
        {"systemId": "SN-0001", "title": "t", "client": "c", "id": 991, "createdAt": "x"}
    )
    assert "id" not in parsed.model_dump()

    with pytest.raises(ValidationError):
        FrontuWorkspaceUpsert.model_validate({"title": "t", "client": "c"})
