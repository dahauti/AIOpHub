"""FrontuTasksClient: task delta paging, upserts, materials/works and catalog cache."""

import json

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from src.adapters.rate_limit import RatePacer
from src.adapters.resilience import ExternalServiceError
from src.clients.frontu_task_payloads import FrontuRejectedError
from src.clients.frontu_tasks_client import FrontuTasksClient
from src.domain.frontu_models import FrontuMaterial, FrontuTaskPayload, FrontuWork
from tests.contracts import contract
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
async def test_catalog_lookups_parse_contract_hits_and_cache_them() -> None:
    mock = RecordingHandler(
        sequence(
            _ok(contract("clients_2216")),
            _ok(contract("workspaces_30971")),
            _ok(contract("task_types_details_SERVICE")),
        )
    )
    client = _client(mock)

    assert await client.find_client_guid("2216") == "d5d5789c-c120-11e5-af86-0a610904597d"
    assert await client.find_workspace_guid("30971") == "cbe86409-4a2b-6cd9-bcaa-e9db34dd8533"
    assert await client.find_task_type_guid("SERVICE") == "97d7ea07-4dc9-48f1-9845-005d89430499"
    assert await client.find_client_guid("2216") == "d5d5789c-c120-11e5-af86-0a610904597d"

    assert [str(r.url) for r in mock.requests] == [
        f"{BASE_URL}/clients?systemId=2216",
        f"{BASE_URL}/workspaces?systemId=30971",
        f"{BASE_URL}/task_types/details?systemId=SERVICE",
    ]


@pytest.mark.asyncio
async def test_catalog_lookups_map_contract_not_found_to_cached_none() -> None:
    mock = RecordingHandler(
        sequence(
            _ok(contract("clients_not_found")),
            _ok(contract("workspaces_not_found")),
            httpx.Response(404, json=contract("task_types_details_not_found")),
        )
    )
    client = _client(mock)

    assert await client.find_client_guid("NONEXISTENT_TEST") is None
    assert await client.find_workspace_guid("NONEXISTENT_TEST") is None
    assert await client.find_task_type_guid("NONEXISTENT_TEST") is None
    assert await client.find_task_type_guid("NONEXISTENT_TEST") is None
    assert len(mock.requests) == 3


@pytest.mark.asyncio
async def test_write_4xx_is_rejection_carrying_frontus_message() -> None:
    body: JsonValue = {"error": "title required"}
    mock = RecordingHandler(sequence(httpx.Response(422, json=body)))
    payload = FrontuTaskPayload(title="T", client="c-1")

    with pytest.raises(FrontuRejectedError) as caught:
        await _client(mock).upsert_task(payload)

    assert (caught.value.status_code, caught.value.frontu_message) == (422, "title required")
    assert len(mock.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 500])
async def test_write_auth_or_server_error_is_not_a_rejection(status: int) -> None:
    mock = RecordingHandler(sequence(httpx.Response(status, json={"error": "x"})))

    with pytest.raises(ExternalServiceError) as caught:
        await _client(mock).push_material(FrontuMaterial(systemId="I", task="t", amount=1))

    assert not isinstance(caught.value, FrontuRejectedError)


@pytest.mark.asyncio
async def test_task_type_listing_body_is_unexpected_response() -> None:
    mock = RecordingHandler(sequence(_ok(contract("clients_not_found"))))

    with pytest.raises(ExternalServiceError) as caught:
        await _client(mock).find_task_type_guid("SERVICE")

    assert caught.value.unexpected_response and caught.value.status_code == 200


@pytest.mark.asyncio
async def test_task_type_404_without_contract_body_stays_http_error() -> None:
    mock = RecordingHandler(sequence(httpx.Response(404, text="<html>gone</html>")))

    with pytest.raises(ExternalServiceError) as caught:
        await _client(mock).find_task_type_guid("SERVICE")

    assert caught.value.status_code == 404 and not caught.value.unexpected_response


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
