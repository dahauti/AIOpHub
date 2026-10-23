"""Frontu FSM API v2 adapter for tasks, materials, works and catalog lookups."""

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from pydantic import BaseModel, JsonValue, SecretStr

from src.adapters.rate_limit import RatePacer
from src.adapters.resilience import ExternalServiceError, Sleep, send_with_retry
from src.clients.frontu_task_payloads import (
    SERVICE,
    catalog_guid,
    is_not_found,
    parse_entity,
    parse_listing,
    system_id_for_guid,
    validate_rows,
)
from src.domain.frontu_models import (
    FrontuEntityRef,
    FrontuMaterial,
    FrontuTask,
    FrontuTaskPayload,
    FrontuWork,
)

# Hard ceiling per listing: a bad watermark cannot turn one poll into a runaway API loop.
MAX_PAGES = 50
DELTA_PARAM = "version"
CATALOG_PATHS: dict[str, str] = {
    "client": "/clients",
    "workspace": "/workspaces",
    "taskType": "/task_types/details",
}

CatalogKind = Literal["client", "workspace", "taskType"]


class FrontuTasksClient:
    """Paced, retrying client for the Frontu task endpoints."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: SecretStr,
        *,
        pacer: RatePacer | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._sleep = sleep
        self._pacer = pacer if pacer is not None else RatePacer(sleep=sleep)
        self._catalog: dict[tuple[CatalogKind, str], str | None] = {}
        self._catalog_lock = asyncio.Lock()
        self._client_system_ids: dict[str, str | None] = {}

    async def fetch_task_delta(self, watermark: int) -> list[FrontuTask]:
        """Tasks changed since `version = watermark`, following `next_page` until 0."""
        return await self._paged("/tasks", {DELTA_PARAM: str(watermark)}, FrontuTask)

    async def scan_latest_version(self, watermark: int) -> int | None:
        """Highest task `version` at/after `watermark`, reading at most MAX_PAGES (no raise)."""
        latest: int | None = None
        async for raw_rows, status_code, _ in self._pages("/tasks", {DELTA_PARAM: str(watermark)}):
            for task in validate_rows(raw_rows, FrontuTask, status_code):
                if task.version is not None and (latest is None or task.version > latest):
                    latest = task.version
        return latest

    async def get_task(self, task_guid: str) -> FrontuTask | None:
        """GET /tasks?guid=; only a row whose `guid` matches is returned."""
        response = await self._send("GET", "/tasks", params={"guid": task_guid})
        rows = validate_rows(parse_listing(response)[0], FrontuTask, response.status_code)
        return next((task for task in rows if task.guid == task_guid), None)

    async def upsert_task(self, task: FrontuTaskPayload) -> FrontuTask:
        """POST /tasks; returns the stored task with its `guid` and `code`."""
        response = await self._send("POST", "/tasks", _body(task))
        return parse_entity(response, FrontuTask)

    async def fetch_task_materials(self, task_guid: str) -> list[FrontuMaterial]:
        """GET /materials?task={guid}."""
        return await self._paged("/materials", {"task": task_guid}, FrontuMaterial)

    async def fetch_task_works(self, task_guid: str) -> list[FrontuWork]:
        """GET /works?task={guid}."""
        return await self._paged("/works", {"task": task_guid}, FrontuWork)

    async def push_material(self, material: FrontuMaterial) -> str:
        """POST /materials (`task` in the body); returns the created material's `guid`."""
        response = await self._send("POST", "/materials", _body(material))
        return parse_entity(response, FrontuEntityRef).guid

    async def push_work(self, work: FrontuWork) -> str:
        """POST /works (`task` in the body); returns the created work's `guid`."""
        response = await self._send("POST", "/works", _body(work))
        return parse_entity(response, FrontuEntityRef).guid

    async def find_client_guid(self, system_id: str) -> str | None:
        return await self._lookup("client", system_id)

    async def find_workspace_guid(self, system_id: str) -> str | None:
        return await self._lookup("workspace", system_id)

    async def find_task_type_guid(self, system_id: str) -> str | None:
        return await self._lookup("taskType", system_id)

    async def get_client_system_id(self, client_guid: str) -> str | None:
        """BC customer No (`systemId`) of a Frontu client via GET /clients?guid=; cached."""
        async with self._catalog_lock:
            if client_guid not in self._client_system_ids:
                response = await self._send("GET", "/clients", params={"guid": client_guid})
                rows = parse_listing(response)[0]
                self._client_system_ids[client_guid] = system_id_for_guid(rows, client_guid)
            return self._client_system_ids[client_guid]

    async def _lookup(self, kind: CatalogKind, system_id: str) -> str | None:
        """Catalog GUID by `systemId`; hits and misses are cached for the client's lifetime."""
        key = (kind, system_id)
        async with self._catalog_lock:
            if key not in self._catalog:
                params = {"systemId": system_id}
                try:
                    response = await self._send("GET", CATALOG_PATHS[kind], params=params)
                except ExternalServiceError as exc:
                    if not is_not_found(exc):  # only /task_types/details answers a miss with 404
                        raise
                    self._catalog[key] = None
                else:
                    self._catalog[key] = catalog_guid(response, single=kind == "taskType")
            return self._catalog[key]

    async def _paged[ModelT: BaseModel](
        self, path: str, params: dict[str, str], model: type[ModelT]
    ) -> list[ModelT]:
        """Validated rows of every page; fails closed past MAX_PAGES (no partial listing)."""
        rows: list[ModelT] = []
        truncated = False
        async for raw_rows, status_code, next_page in self._pages(path, params):
            rows.extend(validate_rows(raw_rows, model, status_code))
            truncated = next_page != 0
        if truncated:
            detail = (
                f"{path} listing exceeded the page ceiling of {MAX_PAGES}; "
                "stamp a baseline with `tasks --bootstrap-watermark`"
            )
            raise ExternalServiceError(SERVICE, None, detail)
        return rows

    async def _pages(
        self, path: str, params: dict[str, str]
    ) -> AsyncIterator[tuple[list[JsonValue], int, int]]:
        """(rows, HTTP status, next_page) per page; stops at next_page 0 or after MAX_PAGES."""
        page = 1
        for _ in range(MAX_PAGES):
            response = await self._send("GET", path, params={**params, "page": str(page)})
            raw_rows, next_page = parse_listing(response)
            yield raw_rows, response.status_code, next_page
            if next_page == 0:
                return
            page = next_page

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


def _body(model: BaseModel) -> dict[str, JsonValue]:
    return model.model_dump(mode="json", exclude_none=True)
