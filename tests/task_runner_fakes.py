"""Mocked BC + Frontu HTTP for end-to-end task runner tests (no network, no real pacing)."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue, SecretStr
from sqlalchemy import select

from src.adapters.rate_limit import RatePacer
from src.clients.frontu_tasks_client import FrontuTasksClient
from src.database import get_engine, get_sessionmaker
from src.models.audit import SyncAuditLog
from src.services import task_runner, task_wiring

ORDER: JsonValue = {
    "No": "SO00001",
    "SelltoCustomerNo": "C00010",
    "DocumentStatus": "FRONTU",
    "SystemModifiedAt": "2026-10-21T10:00:00Z",
}
LINE: JsonValue = {"DocumentNo": "SO00001", "LineNo": 10000, "Type": "Item", "No": "I-1"}
TASK: JsonValue = {"guid": "t-1", "systemId": "SO00001", "status": 400, "version": 7}
MATERIAL: JsonValue = {"guid": "m-1", "systemId": "ITEM-9", "task": "t-1", "amount": 2}
FRONTU_GETS: dict[str, list[JsonValue]] = {
    "/clients": [{"guid": "c-1"}],
    "/workspaces": [],
    "/task_types/details": [],
    "/tasks": [TASK],
    "/materials": [MATERIAL],
    "/works": [],
}
FRONTU_PREFIX = "/client/api/v2"


class MockBackends:
    """Serves BC OData and Frontu reads; answers writes as a live backend would."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail_bc_reads = False
        self.frontu_write_status: int | None = None  # e.g. 422: Frontu rejects every POST
        self.frontu_read_status: int | None = None  # e.g. 500: Frontu fails every GET

    @property
    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        self.requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(204)
        frontu_post = request.method == "POST" and request.url.host.endswith("frontu.com")
        if frontu_post and self.frontu_write_status is not None:
            return httpx.Response(self.frontu_write_status, json={"error": "rejected"})
        if request.method == "POST":
            return httpx.Response(201, json={"guid": "created-1", "code": "T-1", "No": "SO9"})
        if request.url.host.endswith("frontu.com"):
            if self.frontu_read_status is not None:
                return httpx.Response(self.frontu_read_status, json={"error": "unavailable"})
            path = request.url.path.removeprefix(FRONTU_PREFIX)
            return httpx.Response(200, json={"items": FRONTU_GETS[path], "next_page": 0})
        if self.fail_bc_reads:
            return httpx.Response(400, json={"error": {"message": "bad filter"}})
        rows = [LINE] if "/TG_Lines" in request.url.path else [ORDER]
        return httpx.Response(200, json={"value": rows})


@pytest.fixture
def backends(temp_database: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MockBackends]:
    """Temp shared database, mocked HTTP transport and an unpaced Frontu client."""
    mock = MockBackends()
    monkeypatch.setattr(
        task_runner,
        "build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(mock)),
    )

    def unpaced(http: httpx.AsyncClient, base_url: str, key: SecretStr) -> FrontuTasksClient:
        return FrontuTasksClient(http, base_url, key, pacer=RatePacer(min_interval=0.0))

    monkeypatch.setattr(task_wiring, "FrontuTasksClient", unpaced)
    yield mock


async def audited_operations() -> list[str]:
    try:
        async with get_sessionmaker()() as session:
            rows = (await session.scalars(select(SyncAuditLog))).all()
    finally:
        await get_engine().dispose()
    operations: list[str] = []
    for row in rows:
        if row.action == "TASK_RUN":
            continue
        assert row.action == "DRY_RUN_TASK" and row.payload is not None
        operation = row.payload["operation"]
        assert isinstance(operation, str)
        operations.append(operation)
    return operations
