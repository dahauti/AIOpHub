"""Frontu adapter: dynamic workspace custom-field resolution (mocked HTTP)."""

import asyncio
import logging

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from src.adapters.frontu_client import FrontuClient, RatePacer
from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import FrontuCustomFieldIds, FrontuWorkspaceUpsert
from tests.fakes import FakeClock, Handler, RecordingHandler

BASE_URL = "https://acme.frontu.com/client/api/v2"
REG_ID = "3f9c2a1e-0b7d-4c55-9e1a-6d2f8b4a7c10"
SITE_ID = "a71e5d08-2c4f-4b9a-8f63-1e0d9c7b2a54"
FIELDS: list[JsonValue] = [
    {"id": REG_ID, "title": "Registracijos Nr.", "type": "string"},
    {"id": SITE_ID, "title": "Padalinio pavadinimas", "type": "string"},
    {"id": "b0000000-0000-4000-8000-000000000000", "title": "Kita", "type": "number", "x": 1},
]


def _client(mock: RecordingHandler, clock: FakeClock) -> FrontuClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    pacer = RatePacer(clock=clock, sleep=clock.sleep)
    return FrontuClient(http, "acme", SecretStr("frontu-token"), pacer=pacer, sleep=clock.sleep)


def _respond(body: JsonValue) -> Handler:
    return lambda _request: httpx.Response(200, json=body)


@pytest.mark.parametrize(
    "body",
    [FIELDS, {"data": FIELDS}, {"next_page": 0, "page": 1, "items": FIELDS}],
    ids=["list", "data-wrapped", "items-paginated"],
)
@pytest.mark.asyncio
async def test_resolves_field_ids_from_list_or_wrapped_response(body: JsonValue) -> None:
    mock = RecordingHandler(_respond(body))

    ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids == FrontuCustomFieldIds(registration_field_id=REG_ID, site_field_id=SITE_ID)
    request = mock.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{BASE_URL}/customfields/workspaces"
    assert request.headers["Authorization"] == "Bearer frontu-token"


@pytest.mark.asyncio
async def test_title_match_is_case_insensitive_and_trimmed() -> None:
    fields: list[JsonValue] = [
        {"id": REG_ID, "title": "  REGISTRACIJOS NR. "},
        {"id": SITE_ID, "title": "padalinio PAVADINIMAS"},
    ]
    mock = RecordingHandler(_respond(fields))

    ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert (ids.registration_field_id, ids.site_field_id) == (REG_ID, SITE_ID)


@pytest.mark.asyncio
async def test_missing_field_is_logged_and_left_unresolved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fields: list[JsonValue] = [{"id": REG_ID, "title": "Registracijos Nr."}]
    mock = RecordingHandler(_respond(fields))

    with caplog.at_level(logging.WARNING, logger="src.adapters.frontu_client"):
        ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids.registration_field_id == REG_ID
    assert ids.site_field_id is None
    assert [r.getMessage() for r in caplog.records] == [
        "Frontu custom field 'Padalinio pavadinimas' not found; its value will be omitted"
    ]


@pytest.mark.asyncio
async def test_resolution_is_cached_across_calls_and_concurrent_callers() -> None:
    mock = RecordingHandler(_respond(FIELDS))
    client = _client(mock, FakeClock())

    first, second = await asyncio.gather(
        client.resolve_custom_fields(), client.resolve_custom_fields()
    )
    third = await client.resolve_custom_fields()

    assert first == second == third
    assert len(mock.requests) == 1


@pytest.mark.asyncio
async def test_malformed_response_raises_domain_error() -> None:
    mock = RecordingHandler(_respond({"unexpected": True}))

    with pytest.raises(ExternalServiceError) as exc_info:
        await _client(mock, FakeClock()).resolve_custom_fields()

    assert exc_info.value.service == "frontu"


@pytest.mark.asyncio
async def test_custom_field_lookup_shares_the_rate_pacer() -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=FIELDS)
        return httpx.Response(201, json={"id": 1})

    clock = FakeClock()
    client = _client(RecordingHandler(route), clock)

    await client.resolve_custom_fields()
    await client.upsert_workspace(FrontuWorkspaceUpsert(systemId="SN", title="T", client="C"))

    assert clock.sleeps == [0.5]


def test_build_custom_values_omits_unresolved_fields_and_empty_values() -> None:
    partial = FrontuCustomFieldIds(registration_field_id=REG_ID, site_field_id=None)
    full = FrontuCustomFieldIds(registration_field_id=REG_ID, site_field_id=SITE_ID)

    assert partial.build_custom_values("ABC123", "Vilnius") == {REG_ID: "ABC123"}
    assert full.build_custom_values("ABC123", "Vilnius") == {REG_ID: "ABC123", SITE_ID: "Vilnius"}
    assert full.build_custom_values(None, "") is None


@pytest.mark.parametrize(
    "title", ["Registracijos Nr.", "  REGISTRACIJOS nr. "], ids=["exact", "case-and-space"]
)
@pytest.mark.asyncio
async def test_live_paginated_items_envelope_resolves_case_insensitively(
    title: str, caplog: pytest.LogCaptureFixture
) -> None:
    body: JsonValue = {
        "next_page": 0,
        "items": [{"id": "guid-1", "title": title, "type": "string"}],
    }
    mock = RecordingHandler(_respond(body))

    with caplog.at_level(logging.WARNING, logger="src.adapters.frontu_client"):
        ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids == FrontuCustomFieldIds(registration_field_id="guid-1", site_field_id=None)
    assert [r.getMessage() for r in caplog.records] == [
        "Frontu custom field 'Padalinio pavadinimas' not found; its value will be omitted"
    ]


@pytest.mark.parametrize("body", [{"next_page": 0}, {"items": None}], ids=["no-list", "null"])
@pytest.mark.asyncio
async def test_envelope_without_field_list_raises_domain_error(body: JsonValue) -> None:
    mock = RecordingHandler(_respond(body))

    with pytest.raises(ExternalServiceError):
        await _client(mock, FakeClock()).resolve_custom_fields()
