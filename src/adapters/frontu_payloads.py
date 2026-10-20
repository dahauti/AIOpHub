"""Frontu response parsing: workspace write results and listing pages."""

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from src.adapters.resilience import ExternalServiceError
from src.schemas.frontu_models import FrontuUpsertResult, FrontuWorkspaceList

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
