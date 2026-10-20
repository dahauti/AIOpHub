"""Business Central OData V4 adapter for SaaS and OnPrem profiles (Entra ID bearer auth)."""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from src.adapters.bc_auth import EntraCredentials, EntraTokenProvider
from src.adapters.resilience import ExternalServiceError, Sleep, send_with_retry
from src.config import Settings
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle, ODataCollection

logger = logging.getLogger(__name__)

SERVICE = "business-central"
PAGE_SIZE = 500
SAAS_SCOPE = "https://api.businesscentral.dynamics.com/.default"
SAAS_ODATA_ROOT = "https://api.businesscentral.dynamics.com/v2.0/{tenant}/{environment}/ODataV4"
VEHICLES_PAGE = "VehicleCard"
SHIP_TO_PAGE = "TG_ShipToAdress"
_QUERY_SAFE = "',:()"
# A `/Company(` (or `/Company%28`) segment already present in a configured root URL.
_COMPANY_SEGMENT = re.compile(r"/Company(?:\(|%28)", re.IGNORECASE)


def odata_literal(value: str) -> str:
    """Quote a string as an OData literal, doubling embedded single quotes."""
    return "'" + value.replace("'", "''") + "'"


def odata_datetime(value: datetime) -> str:
    """Render an aware datetime as an OData DateTimeOffset literal in UTC."""
    if value.tzinfo is None:
        raise ValueError("OData datetime filters require a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class BCConnection(BaseModel):
    """One BC endpoint profile: Entra credentials, OData root and company."""

    model_config = ConfigDict(frozen=True)

    credentials: EntraCredentials
    odata_root: str
    company: str

    @field_validator("odata_root")
    @classmethod
    def _strip_company_segment(cls, value: str) -> str:
        """Keep only the OData root so exactly one Company(...) segment is ever appended."""
        root = _COMPANY_SEGMENT.split(value, maxsplit=1)[0].rstrip("/")
        if root != value.rstrip("/"):
            logger.warning(
                "BC OData base URL contained a Company(...) segment; it was removed so the "
                "configured company is used",
                extra={"service": SERVICE},
            )
        return root

    @property
    def company_url(self) -> str:
        company = quote(odata_literal(self.company), safe="'")
        return f"{self.odata_root}/Company({company})"


def saas_connection(settings: Settings) -> BCConnection:
    """Build the SaaS profile; the OData root is derived from tenant and environment."""
    credentials = EntraCredentials(
        tenant_id=settings.bc_tenant_id_saas,
        client_id=settings.bc_client_id_saas,
        client_secret=settings.bc_client_secret_saas,
        scope=SAAS_SCOPE,
    )
    root = SAAS_ODATA_ROOT.format(
        tenant=quote(settings.bc_tenant_id_saas, safe=""),
        environment=quote(settings.bc_environment_saas, safe=""),
    )
    return BCConnection(
        credentials=credentials, odata_root=root, company=settings.bc_company_id_saas
    )


def onprem_connection(settings: Settings) -> BCConnection:
    """Build the OnPrem profile from its configured OData base URL."""
    credentials = EntraCredentials(
        tenant_id=settings.bc_tenant_id_onprem,
        client_id=settings.bc_client_id_onprem,
        client_secret=settings.bc_client_secret_onprem,
        scope=settings.bc_scope_onprem,
    )
    return BCConnection(
        credentials=credentials,
        odata_root=str(settings.bc_odata_base_url_onprem),
        company=settings.bc_company_id_onprem,
    )


class BCClient:
    """Reads VehicleCard deltas and TG_ShipToAdress rows from one BC profile."""

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

    async def fetch_vehicles(self, modified_since: datetime) -> AsyncIterator[list[BCODataVehicle]]:
        """Yield pages of vehicles with SystemModifiedAt >= `modified_since`, oldest first."""
        delta_filter = f"SystemModifiedAt ge {odata_datetime(modified_since)}"
        skip = 0
        while True:
            params = {
                "$filter": delta_filter,
                "$orderby": "SystemModifiedAt asc,SerialNo asc",
                "$top": str(PAGE_SIZE),
                "$skip": str(skip),
            }
            page = await self._get_rows(VEHICLES_PAGE, params, BCODataVehicle)
            if page:
                yield page
            if len(page) < PAGE_SIZE:
                return
            skip += PAGE_SIZE

    async def get_ship_to(self, customer_no: str, code: str) -> BCODataShipToAddress | None:
        """Return the ship-to address for a customer/code pair, or None if absent."""
        params = {
            "$filter": f"Customer_No eq {odata_literal(customer_no)} "
            f"and Code eq {odata_literal(code)}",
            "$top": "1",
        }
        rows = await self._get_rows(SHIP_TO_PAGE, params, BCODataShipToAddress)
        return rows[0] if rows else None

    async def _get_rows[RowT: BaseModel](
        self, page: str, params: dict[str, str], row_type: type[RowT]
    ) -> list[RowT]:
        query = "&".join(f"{key}={quote(value, safe=_QUERY_SAFE)}" for key, value in params.items())
        token = await self._tokens.get_token()
        request = self._http.build_request(
            "GET",
            f"{self._connection.company_url}/{page}?{query}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        response = await send_with_retry(self._http, request, service=SERVICE, sleep=self._sleep)
        try:
            rows = ODataCollection.model_validate_json(response.content).value
            return [row_type.model_validate(row) for row in rows]
        except ValidationError as exc:
            raise ExternalServiceError(
                SERVICE, response.status_code, f"bad {page} payload"
            ) from exc
