"""Audit log partial with server-side filters (HTTP transport only)."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from src.api.routes_ui import Control
from src.api.templating import TEMPLATES
from src.schemas.audit_filter import AuditQuery
from src.schemas.sync import AuditRecord

INVALID_FILTER = "Invalid filter"

audit_router = APIRouter()


def parse_audit_query(action: str, search: str, date_range: str) -> AuditQuery:
    """Form/query strings -> validated filter; raises ValidationError on unknown values."""
    return AuditQuery.model_validate({"action": action, "search": search, "date_range": date_range})


def _table(
    request: Request,
    query: AuditQuery,
    rows: list[AuditRecord],
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {"rows": rows, "audit_query": query, "audit_error": error}
    return TEMPLATES.TemplateResponse(
        request, "partials/audit_table.html", context, status_code=status_code
    )


@audit_router.get("/partials/audit", response_class=HTMLResponse)
async def audit_partial(
    request: Request,
    control: Control,
    action: Annotated[str, Query(max_length=16)] = "",
    search: Annotated[str, Query(max_length=64)] = "",
    date_range: Annotated[str, Query(alias="range", max_length=8)] = "today",
) -> HTMLResponse:
    """Filtered audit rows: `action` group, SerialNo `search`, `range` = today | 7d | all."""
    try:
        query = parse_audit_query(action, search, date_range)
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.errors()[0]["loc"])
        unprocessable = status.HTTP_422_UNPROCESSABLE_CONTENT
        return _table(request, AuditQuery(), [], f"{INVALID_FILTER}: {field}", unprocessable)
    return _table(request, query, await control.audit(query))
