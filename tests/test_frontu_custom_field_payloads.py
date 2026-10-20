"""Frontu custom fields: live payload shapes (envelopes, identifier keys, integer types)."""

import logging

import pytest
from pydantic import JsonValue

from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import FrontuCustomFieldIds
from tests.fakes import REG_GUID as REG_ID
from tests.fakes import SITE_GUID as SITE_ID
from tests.fakes import FakeClock, RecordingHandler
from tests.fakes import frontu_client as _client
from tests.fakes import json_response as _respond


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


LIVE_ITEM: dict[str, JsonValue] = {
    "title": "Registracijos Nr.",
    "guid": REG_ID,
    "type": 0,
    "notUsedInApp": False,
}


@pytest.mark.parametrize("id_key", ["guid", "id", "field_id"])
@pytest.mark.asyncio
async def test_live_item_shape_resolves_guid_from_any_identifier_key(id_key: str) -> None:
    item: JsonValue = {k: v for k, v in LIVE_ITEM.items() if k != "guid"} | {id_key: REG_ID}
    site: JsonValue = {"title": "Padalinio pavadinimas", id_key: SITE_ID, "type": 1}
    mock = RecordingHandler(_respond({"next_page": 0, "items": [item, site]}))

    ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids == FrontuCustomFieldIds(registration_field_id=REG_ID, site_field_id=SITE_ID)


@pytest.mark.asyncio
async def test_exact_live_payload_resolves_registration_guid() -> None:
    mock = RecordingHandler(_respond({"next_page": 0, "items": [LIVE_ITEM]}))

    ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids.registration_field_id == "3f9c2a1e-0b7d-4c55-9e1a-6d2f8b4a7c10"


@pytest.mark.asyncio
async def test_guid_takes_precedence_when_several_identifier_keys_exist() -> None:
    item: JsonValue = LIVE_ITEM | {"id": "internal-id"}
    mock = RecordingHandler(_respond({"items": [item]}))

    ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids.registration_field_id == REG_ID


@pytest.mark.parametrize(
    "item",
    [{"guid": REG_ID, "type": 0}, {"title": "Registracijos Nr.", "type": 0}],
    ids=["missing-title", "missing-identifier"],
)
@pytest.mark.asyncio
async def test_item_without_title_or_identifier_raises_domain_error(
    item: dict[str, JsonValue],
) -> None:
    mock = RecordingHandler(_respond({"next_page": 0, "items": [item]}))

    with pytest.raises(ExternalServiceError):
        await _client(mock, FakeClock()).resolve_custom_fields()
