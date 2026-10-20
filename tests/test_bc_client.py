"""BC adapter: profile URLs, OData delta paging and ship-to lookup (mocked HTTP)."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.adapters.bc_client import onprem_connection, saas_connection
from src.config import get_settings
from tests.fakes import (
    ONPREM_ROOT,
    FakeClock,
    MockBC,
    bc_client,
    bc_connection,
    drain,
    raw_query,
    sequence,
    vehicle_page,
)


def test_connection_factories_build_profile_urls(isolated_env: None) -> None:
    settings = get_settings()
    saas = saas_connection(settings)
    onprem = onprem_connection(settings)

    assert saas.company_url == (
        "https://api.businesscentral.dynamics.com/v2.0/your-tenant-id-guid/Production"
        "/ODataV4/Company('your-company-guid-or-name')"
    )
    assert saas.credentials.scope == "https://api.businesscentral.dynamics.com/.default"
    assert saas.credentials.client_id == "your-app-registration-client-id"
    assert onprem.company_url == (
        "https://bc.company.local:7048/BC/ODataV4/Company('your-company-name')"
    )
    assert onprem.credentials.scope == settings.bc_scope_onprem
    assert onprem.credentials.client_id == "your-client-id-guid"


def test_company_name_is_odata_quoted_and_url_encoded() -> None:
    url = bc_connection("O'Neil Trucks, UAB").company_url

    assert url == f"{ONPREM_ROOT}/Company('O''Neil%20Trucks%2C%20UAB')"


@pytest.mark.asyncio
async def test_fetch_vehicles_builds_delta_request_with_bearer_token() -> None:
    mock = MockBC(sequence(vehicle_page(1)))

    assert await drain(bc_client(mock, FakeClock())) == [["SN-0001"]]

    request = mock.requests[0]
    assert str(request.url).startswith(f"{ONPREM_ROOT}/Company('CRONUS')/VehicleCard?")
    query = raw_query(request)
    assert "$filter=SystemModifiedAt%20ge%202026-10-19T08:00:00Z" in query
    assert "$orderby=SystemModifiedAt%20asc,SerialNo%20asc" in query
    assert "$top=500" in query
    assert "$skip=0" in query
    assert request.headers["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
async def test_fetch_vehicles_pages_until_short_page() -> None:
    mock = MockBC(sequence(vehicle_page(*range(500)), vehicle_page(500, 501)))

    pages = await drain(bc_client(mock, FakeClock()))

    assert [len(p) for p in pages] == [500, 2]
    assert ["$skip=0" in raw_query(r) for r in mock.requests] == [True, False]
    assert "$skip=500" in raw_query(mock.requests[1])


@pytest.mark.asyncio
async def test_fetch_vehicles_normalises_since_to_utc_and_rejects_naive() -> None:
    mock = MockBC(sequence(vehicle_page(1)))
    client = bc_client(mock, FakeClock())
    since = datetime(2026, 10, 19, 11, 0, tzinfo=timezone(timedelta(hours=3)))

    await drain(client, since)
    assert "ge%202026-10-19T08:00:00Z" in raw_query(mock.requests[0])

    with pytest.raises(ValueError, match="timezone-aware"):
        await drain(client, datetime(2026, 10, 19, 8, 0))


@pytest.mark.asyncio
async def test_get_ship_to_filters_by_customer_and_code() -> None:
    row = {"Customer_No": "C'01", "Code": "VILNIUS", "City": "Vilnius"}
    mock = MockBC(sequence(httpx.Response(200, json={"value": [row]})))

    ship_to = await bc_client(mock, FakeClock()).get_ship_to("C'01", "VILNIUS")

    assert ship_to is not None
    assert ship_to.City == "Vilnius"
    request = mock.requests[0]
    assert str(request.url).startswith(f"{ONPREM_ROOT}/Company('CRONUS')/TG_ShipToAdress?")
    query = raw_query(request)
    assert "$filter=Customer_No%20eq%20'C''01'%20and%20Code%20eq%20'VILNIUS'" in query
    assert "$top=1" in query


@pytest.mark.asyncio
async def test_get_ship_to_returns_none_when_missing() -> None:
    mock = MockBC(sequence(httpx.Response(200, json={"value": []})))

    assert await bc_client(mock, FakeClock()).get_ship_to("C01", "NONE") is None
