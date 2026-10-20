"""BC adapter: Entra token lifecycle and retry policy (mocked HTTP)."""

from urllib.parse import parse_qs

import httpx
import pytest

from src.adapters.resilience import ExternalServiceError
from tests.fakes import TENANT, FakeClock, MockBC, bc_client, drain, sequence, vehicle_page


@pytest.mark.asyncio
async def test_token_request_uses_client_credentials_grant() -> None:
    mock = MockBC(sequence(vehicle_page(1)))

    await drain(bc_client(mock, FakeClock()))

    token_request = mock.token_requests[0]
    assert str(token_request.url) == (
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    )
    assert parse_qs(token_request.content.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["cid"],
        "client_secret": ["s3cret"],
        "scope": ["api://bc/.default"],
    }


@pytest.mark.asyncio
async def test_token_is_cached_and_refreshed_300s_before_expiry() -> None:
    clock = FakeClock()
    pages = sequence(vehicle_page(1), vehicle_page(2), vehicle_page(3))
    mock = MockBC(pages, token_ttl=3600)
    client = bc_client(mock, clock)

    await drain(client)
    clock.now += 3299
    await drain(client)
    assert len(mock.token_requests) == 1

    clock.now += 1
    await drain(client)
    assert len(mock.token_requests) == 2
    assert mock.requests[-1].headers["Authorization"] == "Bearer tok-2"


@pytest.mark.asyncio
async def test_token_endpoint_failure_is_mapped() -> None:
    mock = MockBC(sequence(vehicle_page(1)), token_status=401)

    with pytest.raises(ExternalServiceError) as exc_info:
        await drain(bc_client(mock, FakeClock()))

    assert exc_info.value.service == "entra-id"
    assert exc_info.value.status_code == 401
    assert mock.requests == []


@pytest.mark.asyncio
async def test_retries_429_honouring_retry_after() -> None:
    clock = FakeClock()
    throttled = httpx.Response(429, headers={"Retry-After": "7"})
    mock = MockBC(sequence(throttled, vehicle_page(1)))

    assert await drain(bc_client(mock, clock)) == [["SN-0001"]]
    assert clock.sleeps == [7.0]


@pytest.mark.asyncio
async def test_retries_with_exponential_backoff_and_jitter() -> None:
    clock = FakeClock()
    mock = MockBC(sequence(httpx.Response(503), httpx.Response(503), vehicle_page(1)))

    assert await drain(bc_client(mock, clock)) == [["SN-0001"]]
    first, second = clock.sleeps
    assert 1.0 <= first <= 2.0
    assert 2.0 <= second <= 3.0


@pytest.mark.parametrize("status", [502, 503, 504])
@pytest.mark.asyncio
async def test_retries_gateway_and_unavailable_statuses(status: int) -> None:
    clock = FakeClock()
    mock = MockBC(sequence(httpx.Response(status), vehicle_page(1)))

    assert await drain(bc_client(mock, clock)) == [["SN-0001"]]
    assert len(mock.requests) == 2
    assert len(clock.sleeps) == 1


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts() -> None:
    mock = MockBC(lambda _request: httpx.Response(503))

    with pytest.raises(ExternalServiceError) as exc_info:
        await drain(bc_client(mock, FakeClock()))

    assert exc_info.value.status_code == 503
    assert exc_info.value.service == "business-central"
    assert len(mock.requests) == 5


@pytest.mark.parametrize("status", [400, 401, 404, 500])
@pytest.mark.asyncio
async def test_non_retryable_status_fails_fast(status: int) -> None:
    clock = FakeClock()
    mock = MockBC(lambda _request: httpx.Response(status, text="boom"))

    with pytest.raises(ExternalServiceError) as exc_info:
        await drain(bc_client(mock, clock))

    assert exc_info.value.status_code == status
    assert len(mock.requests) == 1
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_transport_error_is_mapped_without_retry() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock = MockBC(refuse)

    with pytest.raises(ExternalServiceError) as exc_info:
        await drain(bc_client(mock, FakeClock()))

    assert exc_info.value.status_code is None
    assert len(mock.requests) == 1
