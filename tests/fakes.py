"""Deterministic test doubles and mocked-HTTP helpers shared across adapter tests."""

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from pydantic import JsonValue, SecretStr

from src.adapters.bc_auth import EntraCredentials
from src.adapters.bc_client import BCClient, BCConnection
from src.adapters.frontu_client import FrontuClient
from src.adapters.rate_limit import RatePacer

Handler = Callable[[httpx.Request], httpx.Response]

TENANT = "tenant-guid"
ONPREM_ROOT = "https://bc.local:7048/BC/ODataV4"
SINCE = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
REG_GUID = "3f9c2a1e-0b7d-4c55-9e1a-6d2f8b4a7c10"
SITE_GUID = "a71e5d08-2c4f-4b9a-8f63-1e0d9c7b2a54"
FRONTU_BASE_URL = "https://acme.frontu.com/client/api/v2"


class FakeClock:
    """Monotonic clock whose `sleep` advances time instantly and records each wait."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RecordingHandler:
    """MockTransport handler that records every request before delegating."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


class MockBC:
    """MockTransport handler: issues Entra tokens and delegates OData calls."""

    def __init__(self, odata: Handler, token_ttl: int = 3600, token_status: int = 200) -> None:
        self.odata = odata
        self.token_ttl = token_ttl
        self.token_status = token_status
        self.token_requests: list[httpx.Request] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            self.token_requests.append(request)
            token = f"tok-{len(self.token_requests)}"
            body = {"token_type": "Bearer", "expires_in": self.token_ttl, "access_token": token}
            return httpx.Response(self.token_status, json=body)
        self.requests.append(request)
        return self.odata(request)


def bc_connection(company: str = "CRONUS") -> BCConnection:
    credentials = EntraCredentials(
        tenant_id=TENANT,
        client_id="cid",
        client_secret=SecretStr("s3cret"),
        scope="api://bc/.default",
    )
    return BCConnection(credentials=credentials, odata_root=ONPREM_ROOT, company=company)


def bc_client(mock: MockBC, clock: FakeClock, company: str = "CRONUS") -> BCClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    return BCClient(http, bc_connection(company), clock=clock, sleep=clock.sleep)


def vehicle_page(*serials: int) -> httpx.Response:
    rows: list[JsonValue] = [
        {"SerialNo": f"SN-{n:04d}", "SystemModifiedAt": "2026-10-19T08:15:30Z"} for n in serials
    ]
    return httpx.Response(200, json={"@odata.context": "ctx", "value": rows})


def sequence(*responses: httpx.Response) -> Handler:
    queue = list(responses)
    return lambda _request: queue.pop(0)


def raw_query(request: httpx.Request) -> str:
    return request.url.query.decode()


async def drain(client: BCClient, since: datetime = SINCE) -> list[list[str]]:
    return [[v.SerialNo for v in page] async for page in client.fetch_vehicles(since)]


def frontu_client(mock: RecordingHandler, clock: FakeClock) -> FrontuClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    pacer = RatePacer(clock=clock, sleep=clock.sleep)
    return FrontuClient(
        http, FRONTU_BASE_URL, SecretStr("frontu-token"), pacer=pacer, sleep=clock.sleep
    )


def json_response(body: JsonValue) -> Handler:
    return lambda _request: httpx.Response(200, json=body)
