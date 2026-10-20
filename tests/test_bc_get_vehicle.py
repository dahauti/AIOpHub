"""BC adapter: single VehicleCard lookup by SerialNo (mocked HTTP)."""

import httpx
import pytest

from tests.fakes import ONPREM_ROOT, FakeClock, MockBC, bc_client, raw_query, sequence, vehicle_page


@pytest.mark.asyncio
async def test_get_vehicle_filters_by_escaped_serial_and_takes_one() -> None:
    mock = MockBC(sequence(vehicle_page(7)))

    found = await bc_client(mock, FakeClock()).get_vehicle("VSN'7")

    assert found is not None and found.SerialNo == "SN-0007"
    request = mock.requests[0]
    assert str(request.url).startswith(f"{ONPREM_ROOT}/Company('CRONUS')/VehicleCard?")
    query = raw_query(request)
    assert "$filter=SerialNo%20eq%20'VSN''7'" in query
    assert "$top=1" in query
    assert "$skip" not in query
    assert request.headers["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
async def test_get_vehicle_returns_none_when_bc_has_no_match() -> None:
    mock = MockBC(sequence(httpx.Response(200, json={"value": []})))

    assert await bc_client(mock, FakeClock()).get_vehicle("MISSING") is None
