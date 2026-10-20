"""Frontu FSM API v2 adapter: bearer auth, pacing, custom fields and workspace identity."""

import asyncio
import time
from collections.abc import Callable
from urllib.parse import quote

import httpx
from pydantic import JsonValue, SecretStr

from src.adapters.frontu_fields import CUSTOM_FIELDS_PATH, parse_custom_field_ids
from src.adapters.frontu_payloads import (
    SERVICE,
    parse_upsert_result,
    parse_workspace_page,
    valid_workspaces,
)
from src.adapters.rate_limit import RatePacer
from src.adapters.resilience import ExternalServiceError, Sleep, send_with_retry
from src.schemas.frontu_models import (
    FrontuCustomFieldIds,
    FrontuUpsertResult,
    FrontuWorkspaceSummary,
    FrontuWorkspaceUpsert,
)

BASE_URL = "https://{subdomain}.frontu.com/client/api/v2"
WORKSPACE_PAGE_SIZE = 100
# Fail closed rather than resolve identities against a partial index.
MAX_WORKSPACE_PAGES = 1000


class FrontuClient:
    """Paced, retrying client for the Frontu workspace endpoints."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        subdomain: str,
        api_key: SecretStr,
        *,
        pacer: RatePacer | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._base_url = BASE_URL.format(subdomain=subdomain)
        self._api_key = api_key
        self._sleep = sleep
        self._pacer = pacer if pacer is not None else RatePacer(sleep=sleep)
        self._field_ids: FrontuCustomFieldIds | None = None
        self._field_lock = asyncio.Lock()
        self._clock = clock
        self._listing: list[FrontuWorkspaceSummary] | None = None
        self._listed_at = 0.0
        self._listing_lock = asyncio.Lock()

    async def resolve_custom_fields(self) -> FrontuCustomFieldIds:
        """Resolve sync custom-field GUIDs once per client; later calls hit the cache."""
        async with self._field_lock:
            if self._field_ids is None:
                response = await self._send("GET", CUSTOM_FIELDS_PATH)
                self._field_ids = parse_custom_field_ids(response)
            return self._field_ids

    async def upsert_workspace(self, payload: FrontuWorkspaceUpsert) -> FrontuUpsertResult:
        """POST /workspaces (Frontu upserts by systemId); returns status and response object."""
        body = payload.model_dump(mode="json", exclude_none=True)
        return parse_upsert_result(await self._send("POST", "/workspaces", body))

    async def adopt_workspace(
        self, workspace_id: str, payload: FrontuWorkspaceUpsert
    ) -> FrontuUpsertResult:
        """PUT /workspaces/{id} with the full payload, assigning `systemId` to a legacy object."""
        body = payload.model_dump(mode="json", exclude_none=True)
        path = f"/workspaces/{quote(workspace_id, safe='')}"
        result = parse_upsert_result(await self._send("PUT", path, body))
        if self._listing is not None:
            self._listing = [
                w.model_copy(update={"systemId": payload.systemId}) if w.id == workspace_id else w
                for w in self._listing
            ]
        return result

    async def list_workspaces(self, max_age: float = 0.0) -> list[FrontuWorkspaceSummary]:
        """Every `object` workspace; a listing younger than `max_age` seconds is reused.

        Adoptions through this client update the cached listing, so a reused listing never
        offers an already-adopted workspace again.
        """
        async with self._listing_lock:
            now = self._clock()
            if self._listing is None or max_age <= 0 or now - self._listed_at > max_age:
                self._listing = await self._fetch_workspaces()
                self._listed_at = now
            return list(self._listing)

    async def _fetch_workspaces(self) -> list[FrontuWorkspaceSummary]:
        """Page through the listing; raises instead of returning a partial list."""
        found: list[FrontuWorkspaceSummary] = []
        seen = 0
        for page in range(1, MAX_WORKSPACE_PAGES + 1):
            params = {"type": "object", "pageSize": str(WORKSPACE_PAGE_SIZE), "page": str(page)}
            rows, total = parse_workspace_page(
                await self._send("GET", "/workspaces", params=params)
            )
            found.extend(valid_workspaces(rows, page))
            seen += len(rows)
            if len(rows) < WORKSPACE_PAGE_SIZE or (total is not None and seen >= total):
                return found
        detail = f"workspace listing exceeded the page cap of {MAX_WORKSPACE_PAGES}"
        raise ExternalServiceError(SERVICE, None, detail)

    async def _send(
        self,
        method: str,
        path: str,
        body: dict[str, JsonValue] | None = None,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        request = self._http.build_request(
            method,
            f"{self._base_url}{path}",
            headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            json=body,
            params=params,
        )
        return await send_with_retry(
            self._http,
            request,
            service=SERVICE,
            sleep=self._sleep,
            before_attempt=self._pacer.acquire,
        )
