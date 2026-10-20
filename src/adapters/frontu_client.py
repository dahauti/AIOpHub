"""Frontu FSM API v2 adapter with bearer auth, request pacing and custom-field resolution."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

import httpx
from pydantic import JsonValue, SecretStr, TypeAdapter, ValidationError

from src.adapters.resilience import ExternalServiceError, Sleep, send_with_retry
from src.schemas.frontu_models import (
    FrontuCustomField,
    FrontuCustomFieldIds,
    FrontuCustomFieldList,
    FrontuUpsertResult,
    FrontuWorkspaceUpsert,
)

logger = logging.getLogger(__name__)

SERVICE = "frontu"
BASE_URL = "https://{subdomain}.frontu.com/client/api/v2"
MAX_REQUESTS_PER_MINUTE = 150
MIN_INTERVAL_SECONDS = 0.5
REGISTRATION_FIELD_TITLE = "Registracijos Nr."
SITE_FIELD_TITLE = "Padalinio pavadinimas"
_WINDOW_SECONDS = 60.0
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_CUSTOM_FIELDS = TypeAdapter(list[JsonValue] | FrontuCustomFieldList)


class RatePacer:
    """Rolling 60s window cap plus a minimum gap between consecutive calls."""

    def __init__(
        self,
        max_per_minute: int = MAX_REQUESTS_PER_MINUTE,
        min_interval: float = MIN_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._max = max_per_minute
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until one more request is allowed, then record it."""
        async with self._lock:
            wait = self._wait_needed(self._clock())
            if wait > 0:
                await self._sleep(wait)
            now = self._clock()
            self._prune(now)
            self._sent.append(now)

    def _wait_needed(self, now: float) -> float:
        self._prune(now)
        wait = 0.0
        if self._sent:
            wait = self._sent[-1] + self._min_interval - now
        if len(self._sent) >= self._max:
            wait = max(wait, self._sent[0] + _WINDOW_SECONDS - now)
        return max(wait, 0.0)

    def _prune(self, now: float) -> None:
        while self._sent and now - self._sent[0] >= _WINDOW_SECONDS:
            self._sent.popleft()


def _active_fields(raw_fields: list[JsonValue]) -> list[FrontuCustomField]:
    """Validate entries one by one: skip malformed (warning) and inactive (debug) fields."""
    fields: list[FrontuCustomField] = []
    for index, raw in enumerate(raw_fields):
        try:
            field = FrontuCustomField.model_validate(raw)
        except ValidationError as exc:
            logger.warning(
                "Skipping malformed Frontu custom field at index %d (%d validation errors)",
                index,
                exc.error_count(),
                extra={"service": SERVICE},
            )
            continue
        if field.notUsedInApp:
            logger.debug("Ignoring inactive Frontu custom field %r", field.title)
            continue
        fields.append(field)
    return fields


def _field_id(by_title: dict[str, str], title: str) -> str | None:
    field_id = by_title.get(title.strip().lower())
    if field_id is None:
        logger.warning(
            "Frontu custom field %r not found; its value will be omitted",
            title,
            extra={"service": SERVICE, "field_title": title},
        )
    return field_id


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
    ) -> None:
        self._http = http
        self._base_url = BASE_URL.format(subdomain=subdomain)
        self._api_key = api_key
        self._sleep = sleep
        self._pacer = pacer if pacer is not None else RatePacer(sleep=sleep)
        self._field_ids: FrontuCustomFieldIds | None = None
        self._field_lock = asyncio.Lock()

    async def resolve_custom_fields(self) -> FrontuCustomFieldIds:
        """Resolve sync custom-field GUIDs once per client; later calls hit the cache."""
        async with self._field_lock:
            if self._field_ids is None:
                self._field_ids = await self._fetch_custom_field_ids()
            return self._field_ids

    async def upsert_workspace(self, payload: FrontuWorkspaceUpsert) -> FrontuUpsertResult:
        """POST /workspaces (Frontu upserts by systemId); returns status and response object."""
        body = payload.model_dump(mode="json", exclude_none=True)
        response = await self._send("POST", "/workspaces", body)
        try:
            body = _JSON_OBJECT.validate_json(response.content)
        except ValidationError as exc:
            detail = "workspace response is not a JSON object"
            raise ExternalServiceError(SERVICE, response.status_code, detail) from exc
        return FrontuUpsertResult(status_code=response.status_code, body=body)

    async def _fetch_custom_field_ids(self) -> FrontuCustomFieldIds:
        response = await self._send("GET", "/customfields/workspaces")
        try:
            parsed = _CUSTOM_FIELDS.validate_json(response.content)
        except ValidationError as exc:
            detail = "unexpected custom fields payload"
            raise ExternalServiceError(SERVICE, response.status_code, detail) from exc
        raw_fields = parsed.raw_fields if isinstance(parsed, FrontuCustomFieldList) else parsed
        by_title: dict[str, str] = {}
        for field in _active_fields(raw_fields):
            by_title.setdefault(field.title.strip().lower(), field.id)
        return FrontuCustomFieldIds(
            registration_field_id=_field_id(by_title, REGISTRATION_FIELD_TITLE),
            site_field_id=_field_id(by_title, SITE_FIELD_TITLE),
        )

    async def _send(
        self, method: str, path: str, body: dict[str, JsonValue] | None = None
    ) -> httpx.Response:
        request = self._http.build_request(
            method,
            f"{self._base_url}{path}",
            headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            json=body,
        )
        return await send_with_retry(
            self._http,
            request,
            service=SERVICE,
            sleep=self._sleep,
            before_attempt=self._pacer.acquire,
        )
