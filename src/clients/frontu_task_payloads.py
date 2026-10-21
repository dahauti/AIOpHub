"""Frontu task response parsing: task entities, paged listings and raw-row validation."""

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from src.adapters.resilience import ExternalServiceError
from src.domain.frontu_models import FrontuTaskList

SERVICE = "frontu"
_LISTING = TypeAdapter(list[JsonValue] | FrontuTaskList)


def parse_entity[ModelT: BaseModel](response: httpx.Response, model: type[ModelT]) -> ModelT:
    """Validate a single-object response body."""
    try:
        return model.model_validate_json(response.content)
    except ValidationError as exc:
        detail = f"unexpected {model.__name__} payload"
        raise ExternalServiceError(SERVICE, response.status_code, detail) from exc


def parse_listing(response: httpx.Response) -> tuple[list[JsonValue], int]:
    """Rows plus `next_page` (0 for a bare JSON array, which is never paged)."""
    try:
        parsed = _LISTING.validate_json(response.content)
    except ValidationError as exc:
        detail = "unexpected listing payload"
        raise ExternalServiceError(SERVICE, response.status_code, detail) from exc
    if isinstance(parsed, FrontuTaskList):
        return parsed.items, parsed.next_page
    return parsed, 0


def validate_rows[ModelT: BaseModel](
    rows: list[JsonValue], model: type[ModelT], status_code: int
) -> list[ModelT]:
    """Validate every row; one malformed row fails the call rather than being dropped silently."""
    try:
        return [model.model_validate(row) for row in rows]
    except ValidationError as exc:
        detail = f"unexpected {model.__name__} row"
        raise ExternalServiceError(SERVICE, status_code, detail) from exc


def first_guid(rows: list[JsonValue]) -> str | None:
    """The `guid` of the first row, or None when the lookup matched nothing."""
    for row in rows:
        if isinstance(row, dict):
            guid = row.get("guid")
            if isinstance(guid, str) and guid.strip():
                return guid
    return None


def system_id_for_guid(rows: list[JsonValue], guid: str) -> str | None:
    """Non-blank `systemId` of the row whose `guid` matches, or None."""
    for row in rows:
        if isinstance(row, dict) and row.get("guid") == guid:
            system_id = row.get("systemId")
            if isinstance(system_id, str | int) and str(system_id).strip():
                return str(system_id).strip()
    return None
