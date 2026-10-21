"""Frontu adapter: base URL, bearer auth, upsert contract and rate pacing (mocked HTTP)."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from src.adapters.frontu_client import FrontuClient
from src.adapters.rate_limit import RatePacer
from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import FrontuWorkspaceUpsert
from tests.fakes import FakeClock, RecordingHandler

BASE_URL = "https://acme.frontu.com/client/api/v2"
WORKSPACE = FrontuWorkspaceUpsert(systemId="SN-0001", title="VOLVO FH16", client="C00010")


def _client(mock: RecordingHandler, clock: FakeClock) -> FrontuClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    pacer = RatePacer(clock=clock, sleep=clock.sleep)
    return FrontuClient(http, BASE_URL, SecretStr("frontu-token"), pacer=pacer, sleep=clock.sleep)


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"id": 991, "systemId": "SN-0001"})


@pytest.mark.asyncio
async def test_upsert_posts_workspace_with_bearer_token() -> None:
    mock = RecordingHandler(_ok)

    result = await _client(mock, FakeClock()).upsert_workspace(WORKSPACE)

    assert result.status_code == 201
    assert result.body == {"id": 991, "systemId": "SN-0001"}
    request = mock.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/workspaces"
    assert request.headers["Authorization"] == "Bearer frontu-token"
    assert json.loads(request.content) == {
        "systemId": "SN-0001",
        "title": "VOLVO FH16",
        "client": "C00010",
        "type": "object",
        "deleted": False,
    }


@pytest.mark.asyncio
async def test_blocked_vehicle_is_sent_with_deleted_flag() -> None:
    mock = RecordingHandler(_ok)
    blocked = WORKSPACE.model_copy(update={"deleted": True})

    await _client(mock, FakeClock()).upsert_workspace(blocked)

    assert json.loads(mock.requests[0].content)["deleted"] is True


@pytest.mark.asyncio
async def test_upsert_maps_client_errors_without_retry() -> None:
    clock = FakeClock()
    mock = RecordingHandler(lambda _request: httpx.Response(422, json={"error": "invalid"}))

    with pytest.raises(ExternalServiceError) as exc_info:
        await _client(mock, clock).upsert_workspace(WORKSPACE)

    assert exc_info.value.service == "frontu"
    assert exc_info.value.status_code == 422
    assert len(mock.requests) == 1


@pytest.mark.asyncio
async def test_upsert_rejects_non_object_response() -> None:
    mock = RecordingHandler(lambda _request: httpx.Response(200, json=[1, 2]))

    with pytest.raises(ExternalServiceError):
        await _client(mock, FakeClock()).upsert_workspace(WORKSPACE)


@pytest.mark.asyncio
async def test_upsert_retries_429_and_paces_every_attempt() -> None:
    clock = FakeClock()
    responses = [httpx.Response(429, headers={"Retry-After": "0"}), _ok(httpx.Request("GET", "/"))]
    mock = RecordingHandler(lambda _request: responses.pop(0))

    await _client(mock, clock).upsert_workspace(WORKSPACE)

    assert len(mock.requests) == 2
    assert [s for s in clock.sleeps if s > 0] == [0.5]


@pytest.mark.asyncio
async def test_pacer_enforces_minimum_interval() -> None:
    clock = FakeClock()
    pacer = RatePacer(clock=clock, sleep=clock.sleep)

    for _ in range(3):
        await pacer.acquire()

    assert clock.sleeps == [0.5, 0.5]


@pytest.mark.asyncio
async def test_pacer_does_not_wait_when_calls_are_already_spaced() -> None:
    clock = FakeClock()
    pacer = RatePacer(clock=clock, sleep=clock.sleep)

    await pacer.acquire()
    clock.now += 1.0
    await pacer.acquire()

    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_pacer_caps_requests_per_rolling_minute() -> None:
    clock = FakeClock()
    pacer = RatePacer(max_per_minute=150, min_interval=0.0, clock=clock, sleep=clock.sleep)

    for _ in range(150):
        await pacer.acquire()
    assert clock.sleeps == []

    await pacer.acquire()
    assert clock.sleeps == [60.0]


@pytest.mark.asyncio
async def test_pacer_serialises_concurrent_callers() -> None:
    clock = FakeClock()
    pacer = RatePacer(clock=clock, sleep=clock.sleep)

    await asyncio.gather(*(pacer.acquire() for _ in range(3)))

    assert clock.sleeps == [0.5, 0.5]


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (
            httpx.Response(
                404,
                text='<!DOCTYPE html>\n<html lang="lt"><body>Nerasta</body></html>',
                headers={"content-type": "text/html; charset=UTF-8"},
            ),
            "frontu returned HTTP 404 (non-JSON response)",
        ),
        (httpx.Response(400, text="Bad request"), "frontu returned HTTP 400 (non-JSON response)"),
        (httpx.Response(422, json={"error": "title required"}), '{"error":"title required"}'),
    ],
    ids=["html-page", "plain-text", "json-kept"],
)
@pytest.mark.asyncio
async def test_error_detail_never_contains_html(response: httpx.Response, detail: str) -> None:
    clock = FakeClock()
    mock = RecordingHandler(lambda _request: response)

    with pytest.raises(ExternalServiceError) as caught:
        await _client(mock, clock).upsert_workspace(WORKSPACE)

    assert caught.value.detail == detail
    assert "<" not in str(caught.value)
