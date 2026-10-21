"""BCTasksClient: ServiceOrders delta, TG_Lines reads and writes (mocked HTTP)."""

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import JsonValue

from src.adapters.resilience import ExternalServiceError
from src.clients.bc_tasks_client import PAGE_SIZE, BCTasksClient
from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from tests.fakes import ONPREM_ROOT, FakeClock, MockBC, bc_connection, raw_query, sequence

COMPANY_URL = f"{ONPREM_ROOT}/Company('CRONUS')"
WATERMARK = "2026-10-21T09:45:00+00:00"


def _client(mock: MockBC, clock: FakeClock | None = None) -> BCTasksClient:
    fake = clock or FakeClock()
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    return BCTasksClient(http, bc_connection(), clock=fake, sleep=fake.sleep)


def _collection(rows: list[JsonValue]) -> httpx.Response:
    return httpx.Response(200, json={"@odata.context": "ctx", "value": rows})


def _orders(*numbers: int) -> httpx.Response:
    return _collection(
        [{"No": f"SO{n:05d}", "DocumentStatus": "FRONTU", "@odata.etag": "W/1"} for n in numbers]
    )


@pytest.mark.asyncio
async def test_fetch_released_orders_filters_frontu_status_and_watermark() -> None:
    mock = MockBC(sequence(_orders(1, 2)))

    orders = await _client(mock).fetch_released_orders(WATERMARK)

    assert [order.No for order in orders] == ["SO00001", "SO00002"]
    request = mock.requests[0]
    assert str(request.url).startswith(f"{COMPANY_URL}/ServiceOrders?")
    query = raw_query(request)
    assert (
        "$filter=DocumentStatus%20eq%20'FRONTU'%20and%20SystemModifiedAt%20ge%20"
        "2026-10-21T09:45:00Z" in query
    )
    assert "$top=500" in query and "$skip=0" in query
    assert request.headers["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
async def test_fetch_released_orders_pages_until_short_page() -> None:
    mock = MockBC(sequence(_orders(*range(PAGE_SIZE)), _orders(900)))

    orders = await _client(mock).fetch_released_orders(WATERMARK)

    assert len(orders) == PAGE_SIZE + 1
    assert "$skip=500" in raw_query(mock.requests[1])
    assert len(mock.token_requests) == 1


@pytest.mark.asyncio
async def test_fetch_released_orders_rejects_naive_or_invalid_watermark() -> None:
    client = _client(MockBC(sequence()))

    with pytest.raises(ValueError, match="timezone-aware"):
        await client.fetch_released_orders("2026-10-21T09:45:00")
    with pytest.raises(ValueError, match="ISO 8601"):
        await client.fetch_released_orders("yesterday")


@pytest.mark.asyncio
async def test_fetch_order_lines_filters_by_document_no() -> None:
    row: JsonValue = {"DocumentNo": "SO'1", "LineNo": 10000, "Type": "Item", "Quantity": 2.5}
    mock = MockBC(sequence(_collection([row])))

    lines = await _client(mock).fetch_order_lines("SO'1")

    assert lines[0].Quantity == Decimal("2.5")
    query = raw_query(mock.requests[0])
    assert "$filter=DocumentNo%20eq%20'SO''1'" in query
    assert "$orderby=LineNo%20asc" in query


@pytest.mark.asyncio
async def test_update_order_header_patches_with_wildcard_if_match() -> None:
    mock = MockBC(sequence(httpx.Response(204)))

    await _client(mock).update_order_header("SO00001", {"DocumentStatus": "TASKERYJE"})

    request = mock.requests[0]
    assert request.method == "PATCH"
    assert str(request.url) == (f"{COMPANY_URL}/ServiceOrders(Document_Type='Order',No='SO00001')")
    assert request.headers["If-Match"] == "*"
    assert json.loads(request.content) == {"DocumentStatus": "TASKERYJE"}


@pytest.mark.asyncio
async def test_update_order_header_sends_known_etag() -> None:
    mock = MockBC(sequence(httpx.Response(204)))

    await _client(mock).update_order_header("SO00001", {"Tasker_ID": "g-1"}, etag='W/"JzQ0Oyc="')

    assert mock.requests[0].headers["If-Match"] == 'W/"JzQ0Oyc="'


@pytest.mark.asyncio
async def test_create_service_order_omits_no_and_returns_generated_number() -> None:
    mock = MockBC(sequence(httpx.Response(201, json={"No": "SO00077", "DealType": "SERVICE"})))
    header = BCServiceOrderHeader(DealType="SERVICE", DocumentStatus="IN PROGRESS")

    assert await _client(mock).create_service_order(header) == "SO00077"

    request = mock.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{COMPANY_URL}/ServiceOrders"
    assert json.loads(request.content) == {"DealType": "SERVICE", "DocumentStatus": "IN PROGRESS"}


@pytest.mark.asyncio
async def test_create_service_order_rejects_response_without_number() -> None:
    mock = MockBC(sequence(httpx.Response(201, json={"DealType": "SERVICE"})))

    with pytest.raises(ExternalServiceError, match="returned no order No"):
        await _client(mock).create_service_order(BCServiceOrderHeader())


@pytest.mark.asyncio
async def test_update_order_line_patches_compound_key() -> None:
    mock = MockBC(sequence(httpx.Response(204)))

    await _client(mock).update_order_line("SO'1", 20000, {"Frontu_Integration_Line_GUID": "g"})

    request = mock.requests[0]
    assert request.method == "PATCH"
    assert str(request.url) == (
        f"{COMPANY_URL}/TG_Lines(Document_Type='Order',DocumentNo='SO''1',LineNo=20000)"
    )
    assert request.headers["If-Match"] == "*"


@pytest.mark.asyncio
async def test_fetch_order_returns_match_or_none() -> None:
    mock = MockBC(sequence(_orders(5), _collection([])))
    client = _client(mock)

    found = await client.fetch_order("SO00005")
    assert found is not None and found.No == "SO00005"
    assert await client.fetch_order("SO09999") is None
    assert "$filter=No%20eq%20'SO00005'" in raw_query(mock.requests[0])


@pytest.mark.asyncio
async def test_create_order_line_posts_numeric_decimals() -> None:
    mock = MockBC(sequence(httpx.Response(201, json={})))
    line = BCServiceOrderLine(
        DocumentNo="SO00001", LineNo=20000, Description="Note", Quantity=Decimal("1.25")
    )

    await _client(mock).create_order_line(line)

    request = mock.requests[0]
    assert str(request.url) == f"{COMPANY_URL}/TG_Lines"
    assert json.loads(request.content) == {
        "DocumentNo": "SO00001",
        "LineNo": 20000,
        "Type": " ",
        "Description": "Note",
        "Quantity": 1.25,
        "UnitPrice": 0.0,
        "LineAmount": 0.0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_retries_throttling_and_gateway_errors(status: int) -> None:
    clock = FakeClock()
    mock = MockBC(sequence(httpx.Response(status), _orders(1)))

    orders = await _client(mock, clock).fetch_released_orders(WATERMARK)

    assert [order.No for order in orders] == ["SO00001"]
    assert len(mock.requests) == 2
    assert len(clock.sleeps) == 1


@pytest.mark.asyncio
async def test_non_retryable_error_maps_to_domain_error() -> None:
    mock = MockBC(sequence(httpx.Response(400, json={"error": {"message": "bad"}})))

    with pytest.raises(ExternalServiceError) as error:
        await _client(mock).fetch_order_lines("SO00001")

    assert error.value.status_code == 400
    assert len(mock.requests) == 1
