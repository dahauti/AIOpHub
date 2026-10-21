"""BC OData V4 adapter for `ServiceOrders` and `TG_Lines` (SPEC_TASKS sections 1 and 5)."""

import asyncio
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from src.adapters.bc_auth import EntraTokenProvider
from src.adapters.bc_client import BCConnection, odata_datetime, odata_literal
from src.adapters.resilience import ExternalServiceError, Sleep, send_with_retry
from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.schemas.bc_vehicle import ODataCollection

SERVICE = "business-central"
PAGE_SIZE = 500
ORDERS_PAGE = "ServiceOrders"
LINES_PAGE = "TG_Lines"
DISPATCH_STATUS = "FRONTU"
DOCUMENT_TYPE = "Order"
_QUERY_SAFE = "',:()"
_KEY_SAFE = "',=()"
# Server-maintained fields never sent on create (`No` comes from the BC number series).
_CREATE_EXCLUDE = {"No", "SystemModifiedAt"}


def _parse_watermark(watermark: str) -> datetime:
    try:
        value = datetime.fromisoformat(watermark)
    except ValueError as exc:
        raise ValueError(f"watermark is not an ISO 8601 datetime: {watermark!r}") from exc
    if value.tzinfo is None:
        raise ValueError("watermark must be timezone-aware")
    return value


def order_key(order_no: str) -> str:
    """Compound key `ServiceOrders(Document_Type='Order',No='...')`, URL-encoded."""
    key = f"Document_Type={odata_literal(DOCUMENT_TYPE)},No={odata_literal(order_no)}"
    return quote(f"{ORDERS_PAGE}({key})", safe=_KEY_SAFE)


def line_key(order_no: str, line_no: int) -> str:
    """Compound key `TG_Lines(Document_Type='Order',DocumentNo='...',LineNo=n)`, URL-encoded."""
    key = (
        f"Document_Type={odata_literal(DOCUMENT_TYPE)},"
        f"DocumentNo={odata_literal(order_no)},LineNo={line_no}"
    )
    return quote(f"{LINES_PAGE}({key})", safe=_KEY_SAFE)


class BCTasksClient:
    """Reads dispatched Service Orders and lines; writes headers, new orders and lines."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        connection: BCConnection,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._connection = connection
        self._sleep = sleep
        self._tokens = EntraTokenProvider(http, connection.credentials, clock=clock, sleep=sleep)

    async def fetch_released_orders(self, watermark: str) -> list[BCServiceOrderHeader]:
        """Orders with `DocumentStatus = 'FRONTU'` modified at/after `watermark` (ISO 8601)."""
        since = odata_datetime(_parse_watermark(watermark))
        delta_filter = (
            f"DocumentStatus eq {odata_literal(DISPATCH_STATUS)} and SystemModifiedAt ge {since}"
        )
        orders: list[BCServiceOrderHeader] = []
        skip = 0
        while True:
            params = {
                "$filter": delta_filter,
                "$orderby": "SystemModifiedAt asc,No asc",
                "$top": str(PAGE_SIZE),
                "$skip": str(skip),
            }
            page = await self._get_rows(ORDERS_PAGE, params, BCServiceOrderHeader)
            orders.extend(page)
            if len(page) < PAGE_SIZE:
                return orders
            skip += PAGE_SIZE

    async def fetch_order(self, order_no: str) -> BCServiceOrderHeader | None:
        """The Service Order with this No, or None if BC has no match."""
        params = {"$filter": f"No eq {odata_literal(order_no)}", "$top": "1"}
        rows = await self._get_rows(ORDERS_PAGE, params, BCServiceOrderHeader)
        return rows[0] if rows else None

    async def fetch_order_lines(self, order_no: str) -> list[BCServiceOrderLine]:
        """All `TG_Lines` rows of one Service Order, in line order."""
        params = {"$filter": f"DocumentNo eq {odata_literal(order_no)}", "$orderby": "LineNo asc"}
        return await self._get_rows(LINES_PAGE, params, BCServiceOrderLine)

    async def update_order_header(
        self, order_no: str, patch_data: dict[str, JsonValue], etag: str | None = None
    ) -> None:
        """PATCH header fields; `If-Match` carries `etag` when known, otherwise `*`."""
        url = f"{self._connection.company_url}/{order_key(order_no)}"
        await self._send("PATCH", url, patch_data, {"If-Match": etag or "*"})

    async def update_order_line(
        self,
        order_no: str,
        line_no: int,
        patch_data: dict[str, JsonValue],
        etag: str | None = None,
    ) -> None:
        """PATCH line fields; `If-Match` carries `etag` when known, otherwise `*`."""
        url = f"{self._connection.company_url}/{line_key(order_no, line_no)}"
        await self._send("PATCH", url, patch_data, {"If-Match": etag or "*"})

    async def create_service_order(self, header: BCServiceOrderHeader) -> str:
        """POST a new order; BC assigns `No` from its number series and it is returned."""
        body = header.model_dump(mode="json", exclude_none=True, exclude=_CREATE_EXCLUDE)
        response = await self._send("POST", self._page_url(ORDERS_PAGE), body)
        created = _parse_entity(response, BCServiceOrderHeader, ORDERS_PAGE)
        if not created.No:
            detail = f"{ORDERS_PAGE} create returned no order No"
            raise ExternalServiceError(SERVICE, response.status_code, detail)
        return created.No

    async def create_order_line(self, line: BCServiceOrderLine) -> None:
        """POST one `TG_Lines` row."""
        body = line.model_dump(mode="json", exclude_none=True)
        await self._send("POST", self._page_url(LINES_PAGE), body)

    def _page_url(self, page: str) -> str:
        return f"{self._connection.company_url}/{page}"

    async def _get_rows[RowT: BaseModel](
        self, page: str, params: dict[str, str], row_type: type[RowT]
    ) -> list[RowT]:
        query = "&".join(f"{key}={quote(value, safe=_QUERY_SAFE)}" for key, value in params.items())
        response = await self._send("GET", f"{self._page_url(page)}?{query}")
        try:
            rows = ODataCollection.model_validate_json(response.content).value
            return [row_type.model_validate(row) for row in rows]
        except ValidationError as exc:
            raise ExternalServiceError(
                SERVICE, response.status_code, f"bad {page} payload"
            ) from exc

    async def _send(
        self,
        method: str,
        url: str,
        body: dict[str, JsonValue] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        token = await self._tokens.get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        headers.update(extra_headers or {})
        request = self._http.build_request(method, url, headers=headers, json=body)
        return await send_with_retry(self._http, request, service=SERVICE, sleep=self._sleep)


def _parse_entity[RowT: BaseModel](
    response: httpx.Response, row_type: type[RowT], page: str
) -> RowT:
    try:
        return row_type.model_validate_json(response.content)
    except ValidationError as exc:
        raise ExternalServiceError(SERVICE, response.status_code, f"bad {page} entity") from exc
