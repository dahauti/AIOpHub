"""Audit table partial: all recent rows, or today's failures in full (HTTP transport only)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from src.api.routes_ui import TEMPLATES, Control

AuditFilter = Literal["FAILED"]

audit_router = APIRouter()


@audit_router.get("/partials/audit", response_class=HTMLResponse)
async def audit_partial(
    request: Request, control: Control, status: Annotated[AuditFilter | None, Query()] = None
) -> HTMLResponse:
    """`?status=FAILED` lists today's failures (the "Errors today" KPI) with full messages."""
    if status == "FAILED":
        rows = await control.failed_audit()
    else:
        rows = (await control.snapshot()).recent
    context = {"rows": rows, "audit_filter": status}
    return TEMPLATES.TemplateResponse(request, "partials/audit_table.html", context)
