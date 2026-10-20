"""Frontu workspace custom-field resolution: parse GET /customfields/workspaces into GUIDs."""

import logging

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import (
    FrontuCustomField,
    FrontuCustomFieldIds,
    FrontuCustomFieldList,
)

logger = logging.getLogger(__name__)

SERVICE = "frontu"
CUSTOM_FIELDS_PATH = "/customfields/workspaces"
REGISTRATION_FIELD_TITLE = "Registracijos Nr."
SITE_FIELD_TITLE = "Padalinio pavadinimas"
_CUSTOM_FIELDS = TypeAdapter(list[JsonValue] | FrontuCustomFieldList)


def _active_fields(raw_fields: list[JsonValue]) -> list[FrontuCustomField]:
    """Validate entries one by one: skip malformed (warning) and inactive (debug) fields."""
    fields: list[FrontuCustomField] = []
    for index, raw in enumerate(raw_fields):
        try:
            field = FrontuCustomField.model_validate(raw)
        except ValidationError as exc:
            logger.warning(
                "Skipping malformed Frontu custom field at index %d (%d validation errors)",
                index,
                exc.error_count(),
                extra={"service": SERVICE},
            )
            continue
        if field.notUsedInApp:
            logger.debug("Ignoring inactive Frontu custom field %r", field.title)
            continue
        fields.append(field)
    return fields


def _field_id(by_title: dict[str, str], title: str) -> str | None:
    field_id = by_title.get(title.strip().lower())
    if field_id is None:
        logger.warning(
            "Frontu custom field %r not found; its value will be omitted",
            title,
            extra={"service": SERVICE, "field_title": title},
        )
    return field_id


def parse_custom_field_ids(response: httpx.Response) -> FrontuCustomFieldIds:
    """Resolve the sync's custom-field GUIDs by (case-insensitive) title."""
    try:
        parsed = _CUSTOM_FIELDS.validate_json(response.content)
    except ValidationError as exc:
        detail = "unexpected custom fields payload"
        raise ExternalServiceError(SERVICE, response.status_code, detail) from exc
    raw_fields = parsed.raw_fields if isinstance(parsed, FrontuCustomFieldList) else parsed
    by_title: dict[str, str] = {}
    for field in _active_fields(raw_fields):
        by_title.setdefault(field.title.strip().lower(), field.id)
    return FrontuCustomFieldIds(
        registration_field_id=_field_id(by_title, REGISTRATION_FIELD_TITLE),
        site_field_id=_field_id(by_title, SITE_FIELD_TITLE),
    )
