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


SITE_ITEM: dict[str, JsonValue] = {"title": "Padalinio pavadinimas", "guid": SITE_ID, "type": 1}
MALFORMED_LOG = "Skipping malformed Frontu custom field at index %d (%d validation errors)"


@pytest.mark.parametrize(
    "bad_item",
    [
        {"guid": "no-title", "type": 0},
        {"title": "Kita", "type": 0},
        {"title": None, "guid": "null-title"},
        "not-an-object",
    ],
    ids=["missing-title", "missing-identifier", "null-title", "non-object"],
)
@pytest.mark.asyncio
async def test_malformed_items_are_skipped_with_warning_and_valid_ones_resolve(
    bad_item: JsonValue, caplog: pytest.LogCaptureFixture
) -> None:
    mock = RecordingHandler(_respond({"next_page": 0, "items": [bad_item, LIVE_ITEM, SITE_ITEM]}))

    with caplog.at_level(logging.WARNING, logger="src.adapters.frontu_client"):
        ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids == FrontuCustomFieldIds(registration_field_id=REG_ID, site_field_id=SITE_ID)
    assert [(r.msg, r.args[0]) for r in caplog.records if isinstance(r.args, tuple)] == [
        (MALFORMED_LOG, 0)
    ]


@pytest.mark.asyncio
async def test_inactive_field_is_ignored_even_when_title_matches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inactive = SITE_ITEM | {"notUsedInApp": True}
    mock = RecordingHandler(_respond({"items": [LIVE_ITEM, inactive]}))

    with caplog.at_level(logging.WARNING, logger="src.adapters.frontu_client"):
        ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids == FrontuCustomFieldIds(registration_field_id=REG_ID, site_field_id=None)
    assert [r.getMessage() for r in caplog.records] == [
        "Frontu custom field 'Padalinio pavadinimas' not found; its value will be omitted"
    ]


@pytest.mark.asyncio
async def test_active_field_wins_over_earlier_inactive_duplicate() -> None:
    archived = LIVE_ITEM | {"guid": "archived-guid", "notUsedInApp": True}
    mock = RecordingHandler(_respond({"items": [archived, LIVE_ITEM]}))

    ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids.registration_field_id == REG_ID


@pytest.mark.asyncio
async def test_envelope_items_that_are_not_a_list_still_fail_fast() -> None:
    mock = RecordingHandler(_respond({"next_page": 0, "items": {"guid": REG_ID}}))

    with pytest.raises(ExternalServiceError):
        await _client(mock, FakeClock()).resolve_custom_fields()


@pytest.mark.parametrize("flag", [False, None], ids=["false", "null"])
@pytest.mark.asyncio
async def test_field_with_false_or_null_not_used_flag_counts_as_active(flag: JsonValue) -> None:
    mock = RecordingHandler(_respond({"items": [LIVE_ITEM | {"notUsedInApp": flag}]}))

    ids = await _client(mock, FakeClock()).resolve_custom_fields()

    assert ids.registration_field_id == REG_ID
