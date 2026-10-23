"""Frontu task response parsing: task entities, paged listings and raw-row validation."""

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from src.adapters.link_health import LINK_FAILURE_CODES
from src.adapters.resilience import ExternalServiceError
from src.domain.frontu_models import FrontuCatalogEntry, FrontuErrorBody, FrontuTaskList
from src.services.redaction import sanitize

SERVICE = "frontu"
_LISTING = TypeAdapter(list[JsonValue] | FrontuTaskList)


class FrontuRejectedError(ExternalServiceError):
    """Frontu refused a write with 4xx; `frontu_message` is its own reason, sanitized (≤500)."""

    def __init__(self, error: ExternalServiceError) -> None:
        super().__init__(error.service, error.status_code, error.detail, url=error.url)
        try:
            reason = FrontuErrorBody.model_validate_json(error.detail).error
        except ValidationError:
            reason = error.detail  # not the `{"error": ...}` shape: show the body as is
        self.frontu_message = sanitize(reason) or ""


def is_rejection(error: ExternalServiceError) -> bool:
    """4xx except auth/throttling (401/403/429), which stay link failures (system errors)."""
    code = error.status_code
    return code is not None and 400 <= code < 500 and code not in LINK_FAILURE_CODES


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
        FrontuErrorBody.model_validate_json(error.detail)
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
