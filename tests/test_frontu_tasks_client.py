"""FrontuTasksClient: task delta paging, upserts, materials/works and catalog cache."""

import json

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from src.adapters.rate_limit import RatePacer
from src.adapters.resilience import ExternalServiceError
from src.clients.frontu_tasks_client import FrontuTasksClient
from src.domain.frontu_models import FrontuMaterial, FrontuTaskPayload, FrontuWork
from tests.fakes import FakeClock, RecordingHandler, sequence

BASE_URL = "https://acme.frontu.com/client/api/v2"


def _client(mock: RecordingHandler, clock: FakeClock | None = None) -> FrontuTasksClient:
    fake = clock or FakeClock()
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    pacer = RatePacer(clock=fake, sleep=fake.sleep)
    return FrontuTasksClient(
        http, BASE_URL, SecretStr("frontu-token"), pacer=pacer, sleep=fake.sleep
    )


def _page(guids: list[str], next_page: int) -> httpx.Response:
    items: list[JsonValue] = [{"guid": g, "status": 200} for g in guids]
    return httpx.Response(200, json={"items": items, "next_page": next_page})


def _ok(body: JsonValue) -> httpx.Response:
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_fetch_task_delta_follows_next_page_until_zero() -> None:
    mock = RecordingHandler(sequence(_page(["t-1"], 2), _page(["t-2"], 3), _page([], 0)))

    tasks = await _client(mock).fetch_task_delta(1761039900)

    assert [task.guid for task in tasks] == ["t-1", "t-2"]
    assert [r.url.params["page"] for r in mock.requests] == ["1", "2", "3"]
    first = mock.requests[0]
    assert first.url.path == "/client/api/v2/tasks"
    assert dict(first.url.params) == {"version": "1761039900", "page": "1"}
    assert first.headers["Authorization"] == "Bearer frontu-token"


@pytest.mark.asyncio
async def test_fetch_task_delta_fails_on_malformed_row() -> None:
    mock = RecordingHandler(sequence(_ok({"items": [{"code": "no-guid"}], "next_page": 0})))

    with pytest.raises(ExternalServiceError, match="FrontuTask row"):
        await _client(mock).fetch_task_delta(0)


@pytest.mark.asyncio
async def test_upsert_task_posts_payload_and_parses_task() -> None:
    mock = RecordingHandler(sequence(_ok({"guid": "t-9", "code": 1042, "systemId": "SO00001"})))
    payload = FrontuTaskPayload(systemId="SO00001", title="Service", client="c-1")

    task = await _client(mock).upsert_task(payload)

    assert (task.guid, task.code) == ("t-9", "1042")
    request = mock.requests[0]
    assert (request.method, str(request.url)) == ("POST", f"{BASE_URL}/tasks")
    assert json.loads(request.content) == {
        "systemId": "SO00001",
        "title": "Service",
        "client": "c-1",
    }


@pytest.mark.asyncio
async def test_fetch_materials_and_works_accept_list_and_envelope() -> None:
    materials: JsonValue = [{"systemId": "ITEM-1", "task": "t 1", "amount": 2.5}]
    works: JsonValue = {"items": [{"systemId": "RES-1", "task": "t 1", "duration": 45}]}
    mock = RecordingHandler(sequence(_ok(materials), _ok(works)))
    client = _client(mock)

    fetched_materials = await client.fetch_task_materials("t 1")
    fetched_works = await client.fetch_task_works("t 1")

    assert fetched_materials[0].amount == 2.5
    assert fetched_works[0].duration == 45
    assert [(r.url.path, r.url.params["task"]) for r in mock.requests] == [
        ("/client/api/v2/materials", "t 1"),
        ("/client/api/v2/works", "t 1"),
    ]


@pytest.mark.asyncio
async def test_push_material_and_work_post_to_task() -> None:
    mock = RecordingHandler(sequence(_ok({"guid": "fm-1"}), _ok({"guid": "fw-1"})))
    client = _client(mock)
    material = FrontuMaterial(systemId="ITEM-1", task="t-1", amount=1.5, standartizedMaterial="m")
    work = FrontuWork(systemId="RES-1", task="t-1", duration=90, user="u-1")

    assert await client.push_material(material) == "fm-1"
    assert await client.push_work(work) == "fw-1"

    assert [str(r.url) for r in mock.requests] == [f"{BASE_URL}/materials", f"{BASE_URL}/works"]
    assert json.loads(mock.requests[0].content) == {
        "systemId": "ITEM-1",
        "task": "t-1",
        "amount": 1.5,
        "standartizedMaterial": "m",
    }
    assert json.loads(mock.requests[1].content) == {
        "systemId": "RES-1",
        "task": "t-1",
        "user": "u-1",
        "duration": 90,
    }


@pytest.mark.asyncio
async def test_catalog_lookups_hit_each_endpoint_and_cache_hits_and_misses() -> None:
    mock = RecordingHandler(
        sequence(
            _ok({"items": [{"guid": "c-1"}], "next_page": 0}),
            _ok([{"guid": "w-1"}]),
            _ok({"items": [], "next_page": 0}),
        )
    )
    client = _client(mock)

    assert await client.find_client_guid("C00010") == "c-1"
    assert await client.find_workspace_guid("SN-1") == "w-1"
    assert await client.find_task_type_guid("MISSING") is None
    assert await client.find_client_guid("C00010") == "c-1"
    assert await client.find_task_type_guid("MISSING") is None

    assert [str(r.url) for r in mock.requests] == [
        f"{BASE_URL}/clients?systemId=C00010",
        f"{BASE_URL}/workspaces?systemId=SN-1",
        f"{BASE_URL}/task_types/details?systemId=MISSING",
    ]


@pytest.mark.asyncio
async def test_push_rejects_create_response_without_guid() -> None:
    mock = RecordingHandler(sequence(_ok({})))
    material = FrontuMaterial(systemId="ITEM-1", task="t-1", amount=1)

    with pytest.raises(ExternalServiceError, match="FrontuEntityRef"):
        await _client(mock).push_material(material)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_retries_throttling_and_gateway_errors(status: int) -> None:
    mock = RecordingHandler(sequence(httpx.Response(status), _page(["t-1"], 0)))

    tasks = await _client(mock).fetch_task_delta(0)

    assert [task.guid for task in tasks] == ["t-1"]
    assert len(mock.requests) == 2


@pytest.mark.asyncio
async def test_non_json_listing_maps_to_domain_error() -> None:
    mock = RecordingHandler(sequence(httpx.Response(200, text="<html></html>")))

    with pytest.raises(ExternalServiceError, match="unexpected listing payload"):
        await _client(mock).fetch_task_works("t-1")


@pytest.mark.asyncio
async def test_get_client_system_id_matches_guid_and_caches() -> None:
    rows: JsonValue = [{"guid": "other", "systemId": "X"}, {"guid": "c-1", "systemId": 10010}]
    mock = RecordingHandler(sequence(_ok({"items": rows, "next_page": 0}), _ok([])))
    client = _client(mock)

    assert await client.get_client_system_id("c-1") == "10010"
    assert await client.get_client_system_id("c-1") == "10010"
    assert await client.get_client_system_id("c-blank") is None

    assert [str(r.url) for r in mock.requests] == [
        f"{BASE_URL}/clients?guid=c-1",
        f"{BASE_URL}/clients?guid=c-blank",
    ]
