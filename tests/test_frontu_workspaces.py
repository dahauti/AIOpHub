"""Frontu adapter: workspace listing for identity resolution and adoption PUT (mocked HTTP)."""

import json

import httpx
import pytest

from src.adapters.frontu_client import WORKSPACE_PAGE_SIZE
from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from tests.fakes import FakeClock, RecordingHandler, frontu_client

BASE_URL = "https://acme.frontu.com/client/api/v2"


def _item(index: int) -> dict[str, str | int | None]:
    return {"id": index, "systemId": None, "serialNumber": f"VIN-{index}", "type": "object"}


def _paged(pages: list[httpx.Response]) -> RecordingHandler:
    return RecordingHandler(lambda request: pages[int(request.url.params["page"]) - 1])


@pytest.mark.asyncio
async def test_list_pages_until_a_short_page() -> None:
    full = [_item(i) for i in range(WORKSPACE_PAGE_SIZE)]
    mock = _paged(
        [
            httpx.Response(200, json={"items": full}),
            httpx.Response(200, json={"items": [_item(-1)]}),
        ]
    )

    found = await frontu_client(mock, FakeClock()).list_workspaces()

    assert len(found) == WORKSPACE_PAGE_SIZE + 1
    assert found[0].id == "0" and found[0].serialNumber == "VIN-0"
    first = mock.requests[0]
    assert str(first.url).startswith(f"{BASE_URL}/workspaces?")
    assert dict(first.url.params) == {"type": "object", "pageSize": "100", "page": "1"}
    assert [r.url.params["page"] for r in mock.requests] == ["1", "2"]
    assert first.headers["Authorization"] == "Bearer frontu-token"


@pytest.mark.asyncio
async def test_list_stops_when_total_is_reached() -> None:
    full = [_item(i) for i in range(WORKSPACE_PAGE_SIZE)]
    mock = _paged([httpx.Response(200, json={"items": full, "total": WORKSPACE_PAGE_SIZE})])

    found = await frontu_client(mock, FakeClock()).list_workspaces()

    assert len(found) == WORKSPACE_PAGE_SIZE and len(mock.requests) == 1


@pytest.mark.asyncio
async def test_list_accepts_bare_array_and_skips_malformed_items() -> None:
    body = [_item(1), {"systemId": "no-id"}, "garbage", {"id": "w2", "extra": {"x": 1}}]
    mock = _paged([httpx.Response(200, json=body)])

    found = await frontu_client(mock, FakeClock()).list_workspaces()

    assert [(w.id, w.systemId) for w in found] == [("1", None), ("w2", None)]


@pytest.mark.asyncio
async def test_list_rejects_unexpected_envelope() -> None:
    mock = _paged([httpx.Response(200, json={"unexpected": True})])

    with pytest.raises(ExternalServiceError, match="workspace listing"):
        await frontu_client(mock, FakeClock()).list_workspaces()


@pytest.mark.asyncio
async def test_list_fails_closed_when_page_cap_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.adapters.frontu_client.MAX_WORKSPACE_PAGES", 2)
    full = {"items": [_item(i) for i in range(WORKSPACE_PAGE_SIZE)]}
    mock = RecordingHandler(lambda _request: httpx.Response(200, json=full))

    with pytest.raises(ExternalServiceError, match="page cap"):
        await frontu_client(mock, FakeClock()).list_workspaces()
    assert len(mock.requests) == 2


@pytest.mark.asyncio
async def test_adopt_puts_full_payload_to_workspace_id() -> None:
    mock = RecordingHandler(lambda _request: httpx.Response(200, json={"id": "w/7"}))
    payload = FrontuWorkspaceUpsert(
        systemId="0060/00387", title="FARID", client="1738", serialNumber="VIN-1"
    )

    result = await frontu_client(mock, FakeClock()).adopt_workspace("w/7", payload)

    assert (result.status_code, result.body) == (200, {"id": "w/7"})
    request = mock.requests[0]
    assert request.method == "PUT"
    assert str(request.url) == f"{BASE_URL}/workspaces/w%2F7"
    sent = json.loads(request.content)
    assert sent["systemId"] == "0060/00387" and sent["serialNumber"] == "VIN-1"
    assert "address" not in sent and sent["deleted"] is False


@pytest.mark.asyncio
async def test_adopt_maps_client_errors() -> None:
    mock = RecordingHandler(lambda _request: httpx.Response(409, json={"error": "conflict"}))
    payload = FrontuWorkspaceUpsert(systemId="SN-1", title="T", client="C")

    with pytest.raises(ExternalServiceError) as caught:
        await frontu_client(mock, FakeClock()).adopt_workspace("w1", payload)
    assert caught.value.status_code == 409


def _counting_listing() -> RecordingHandler:
    one = {"items": [_item(1)]}
    return RecordingHandler(
        lambda r: httpx.Response(200, json=one if r.method == "GET" else {"id": "1"})
    )


@pytest.mark.asyncio
async def test_listing_is_reused_within_max_age_and_refreshed_after() -> None:
    clock, mock = FakeClock(), _counting_listing()
    client = frontu_client(mock, clock)

    await client.list_workspaces(max_age=300)
    clock.now += 299
    cached = await client.list_workspaces(max_age=300)
    assert len(mock.requests) == 1 and cached[0].id == "1"

    clock.now += 2
    await client.list_workspaces(max_age=300)
    assert len(mock.requests) == 2


@pytest.mark.asyncio
async def test_default_max_age_always_fetches_fresh() -> None:
    mock = _counting_listing()
    client = frontu_client(mock, FakeClock())

    await client.list_workspaces()
    await client.list_workspaces()

    assert len(mock.requests) == 2


@pytest.mark.asyncio
async def test_adoption_marks_the_cached_workspace_as_managed() -> None:
    clock, mock = FakeClock(), _counting_listing()
    client = frontu_client(mock, clock)
    await client.list_workspaces(max_age=300)

    await client.adopt_workspace("1", FrontuWorkspaceUpsert(systemId="SN-1", title="T", client="C"))
    cached = await client.list_workspaces(max_age=300)

    assert [(w.id, w.systemId) for w in cached] == [("1", "SN-1")]
    assert [r.method for r in mock.requests] == ["GET", "PUT"]
