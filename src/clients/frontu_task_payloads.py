"""Frontu task response parsing: task entities, paged listings and raw-row validation."""

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from src.adapters.resilience import ExternalServiceError
from src.domain.frontu_models import FrontuCatalogEntry, FrontuNotFound, FrontuTaskList

SERVICE = "frontu"
_LISTING = TypeAdapter(list[JsonValue] | FrontuTaskList)


def _unexpected(status_code: int, detail: str) -> ExternalServiceError:
    return ExternalServiceError(SERVICE, status_code, detail, unexpected_response=True)


def parse_entity[ModelT: BaseModel](response: httpx.Response, model: type[ModelT]) -> ModelT:
    """Validate a single-object response body."""
    try:
        return model.model_validate_json(response.content)
    except ValidationError as exc:
        raise _unexpected(response.status_code, f"unexpected {model.__name__} payload") from exc


def parse_listing(response: httpx.Response) -> tuple[list[JsonValue], int]:
    """Rows plus `next_page` (0 for a bare JSON array, which is never paged)."""
    try:
        parsed = _LISTING.validate_json(response.content)
    except ValidationError as exc:
        raise _unexpected(response.status_code, "unexpected listing payload") from exc
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
        raise _unexpected(status_code, f"unexpected {model.__name__} row") from exc


def catalog_guid(response: httpx.Response, *, single: bool) -> str | None:
    """GUID of a `systemId` lookup: `single` (task_types/details) is one object; listings
    (`/clients`, `/workspaces`) answer a miss with empty `items` (contracts/frontu/)."""
    if single:
        return parse_entity(response, FrontuCatalogEntry).guid
    rows = validate_rows(parse_listing(response)[0], FrontuCatalogEntry, response.status_code)
    return rows[0].guid if rows else None


def is_not_found(error: ExternalServiceError) -> bool:
    """HTTP 404 with the contract body `{"error": ...}`; any other 404 stays a system error."""
    if error.status_code != 404:
        return False
    try:
        FrontuNotFound.model_validate_json(error.detail)
    except ValidationError:
        return False
    return True


def system_id_for_guid(rows: list[JsonValue], guid: str) -> str | None:
    """Non-blank `systemId` of the row whose `guid` matches, or None."""
    for row in rows:
        if isinstance(row, dict) and row.get("guid") == guid:
            system_id = row.get("systemId")
            if isinstance(system_id, str | int) and str(system_id).strip():
                return str(system_id).strip()
    return None
