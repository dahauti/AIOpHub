"""Frontu response parsing: workspace write results and defensive listing pages."""

import logging

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import (
    WORKSPACE_ID_KEYS,
    FrontuUpsertResult,
    FrontuWorkspaceList,
    FrontuWorkspaceSummary,
)

logger = logging.getLogger(__name__)

SERVICE = "frontu"
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_WORKSPACE_PAGE = TypeAdapter(list[JsonValue] | FrontuWorkspaceList)


def parse_upsert_result(response: httpx.Response) -> FrontuUpsertResult:
    try:
        body = _JSON_OBJECT.validate_json(response.content)
    except ValidationError as exc:
        detail = "workspace response is not a JSON object"
        raise ExternalServiceError(SERVICE, response.status_code, detail) from exc
    return FrontuUpsertResult(status_code=response.status_code, body=body)


def parse_workspace_page(response: httpx.Response) -> tuple[list[JsonValue], int | None]:
    try:
        parsed = _WORKSPACE_PAGE.validate_json(response.content)
    except ValidationError as exc:
        detail = "unexpected workspace listing payload"
        raise ExternalServiceError(SERVICE, response.status_code, detail) from exc
    if isinstance(parsed, FrontuWorkspaceList):
        return parsed.items, parsed.total
    return parsed, None


def _identifiable(raw: JsonValue) -> bool:
    """True when a row carries an identifier or serial number, so it could be a match."""
    if not isinstance(raw, dict):
        return False
    keys = (*WORKSPACE_ID_KEYS, "serialNumber")
    return any(raw.get(key) not in (None, "") for key in keys)


def valid_workspaces(rows: list[JsonValue], page: int) -> list[FrontuWorkspaceSummary]:
    """Validate one listing page, failing closed on any unreadable row that could match.

    Skipping such a row could hide a legacy twin and lead to a duplicate POST, so the whole
    listing is refused instead. Rows with neither identifier nor serial number are skipped.
    """
    valid: list[FrontuWorkspaceSummary] = []
    skipped = 0
    for raw in rows:
        try:
            valid.append(FrontuWorkspaceSummary.model_validate(raw))
        except ValidationError as exc:
            if _identifiable(raw):
                detail = (
                    f"unreadable workspace row on listing page {page} "
                    f"({exc.error_count()} validation errors); refusing a partial identity index"
                )
                raise ExternalServiceError(SERVICE, None, detail) from exc
            skipped += 1
    if skipped:
        logger.warning(
            "Skipped %d unidentifiable Frontu workspace rows on page %d",
            skipped,
            page,
            extra={"service": SERVICE},
        )
    return valid
