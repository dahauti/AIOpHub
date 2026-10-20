"""Frontu adapter: raw workspace listing pages for the operations reports (mocked HTTP)."""

import httpx
import pytest
from pydantic import JsonValue

from src.adapters.frontu_client import WORKSPACE_PAGE_SIZE, FrontuClient
from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import FrontuWorkspaceSummary
from tests.fakes import FakeClock, RecordingHandler, frontu_client

BASE_URL = "https://acme.frontu.com/client/api/v2"


def _item(index: int) -> dict[str, JsonValue]:
    return {"guid": f"g-{index}", "systemId": None, "serialNumber": f"VIN-{index}"}


def _paged(pages: list[httpx.Response]) -> RecordingHandler:
    return RecordingHandler(lambda request: pages[int(request.url.params["page"]) - 1])


async def _rows(client: FrontuClient) -> list[JsonValue]:
    rows: list[JsonValue] = []
    async for _page, page_rows in client.iter_workspace_pages():
        rows.extend(page_rows)
    return rows


@pytest.mark.asyncio
async def test_pages_until_a_short_page() -> None:
    full = {"items": [_item(i) for i in range(WORKSPACE_PAGE_SIZE)], "next_page": 2}
    mock = _paged(
        [httpx.Response(200, json=full), httpx.Response(200, json={"items": [_item(-1)]})]
    )

    rows = await _rows(frontu_client(mock, FakeClock()))

    assert len(rows) == WORKSPACE_PAGE_SIZE + 1
    first = mock.requests[0]
    assert str(first.url).startswith(f"{BASE_URL}/workspaces?")
    assert dict(first.url.params) == {"type": "object", "pageSize": "100", "page": "1"}
    assert [r.url.params["page"] for r in mock.requests] == ["1", "2"]
    assert first.headers["Authorization"] == "Bearer frontu-token"


@pytest.mark.asyncio
async def test_stops_when_total_is_reached() -> None:
    full = [_item(i) for i in range(WORKSPACE_PAGE_SIZE)]
    mock = _paged([httpx.Response(200, json={"items": full, "total": WORKSPACE_PAGE_SIZE})])

    rows = await _rows(frontu_client(mock, FakeClock()))

    assert len(rows) == WORKSPACE_PAGE_SIZE and len(mock.requests) == 1


@pytest.mark.asyncio
async def test_accepts_bare_array() -> None:
    mock = _paged([httpx.Response(200, json=[_item(1), "garbage"])])

    assert await _rows(frontu_client(mock, FakeClock())) == [_item(1), "garbage"]


@pytest.mark.asyncio
async def test_rejects_unexpected_envelope() -> None:
    mock = _paged([httpx.Response(200, json={"unexpected": True})])

    with pytest.raises(ExternalServiceError, match="workspace listing"):
        await _rows(frontu_client(mock, FakeClock()))


@pytest.mark.asyncio
async def test_raises_when_page_cap_is_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.adapters.frontu_client.MAX_WORKSPACE_PAGES", 2)
    full = {"items": [_item(i) for i in range(WORKSPACE_PAGE_SIZE)]}
    mock = RecordingHandler(lambda _request: httpx.Response(200, json=full))

    with pytest.raises(ExternalServiceError, match="page cap"):
        await _rows(frontu_client(mock, FakeClock()))
    assert len(mock.requests) == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"guid": "6f1c-uuid", "id": 7, "serialNumber": "VIN-1"}, "6f1c-uuid"),
        ({"guid": "", "id": 7}, "7"),
        ({"id": "w2", "extra": {"x": 1}}, "w2"),
    ],
    ids=["guid-wins", "blank-guid-falls-back", "id-only"],
)
def test_summary_identifier_prefers_guid(raw: dict[str, JsonValue], expected: str) -> None:
    assert FrontuWorkspaceSummary.model_validate(raw).id == expected
