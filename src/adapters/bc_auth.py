"""Entra ID client-credentials token provider with in-memory caching (SaaS or OnPrem profile)."""

import asyncio
import time
from collections.abc import Callable

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from src.adapters.resilience import ExternalServiceError, Sleep, send_with_retry

SERVICE = "entra-id"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
REFRESH_MARGIN_SECONDS = 300


class EntraCredentials(BaseModel):
    """App registration credentials for one BC connection profile."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    client_id: str
    client_secret: SecretStr
    scope: str


class _TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str
    expires_in: int


class EntraTokenProvider:
    """Caches one bearer token and refreshes it `REFRESH_MARGIN_SECONDS` before expiry."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        credentials: EntraCredentials,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._credentials = credentials
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Return a valid bearer token, fetching a new one only when the cache is stale."""
        async with self._lock:
            if self._token is None or self._clock() >= self._refresh_at:
                self._token = await self._fetch_token()
            return self._token

    async def _fetch_token(self) -> str:
        creds = self._credentials
        issued_at = self._clock()
        request = self._http.build_request(
            "POST",
            TOKEN_URL.format(tenant_id=creds.tenant_id),
            data={
                "grant_type": "client_credentials",
                "client_id": creds.client_id,
                "client_secret": creds.client_secret.get_secret_value(),
                "scope": creds.scope,
            },
        )
        response = await send_with_retry(self._http, request, service=SERVICE, sleep=self._sleep)
        try:
            token = _TokenResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise ExternalServiceError(SERVICE, response.status_code, "malformed token") from exc
        self._refresh_at = issued_at + token.expires_in - REFRESH_MARGIN_SECONDS
        return token.access_token
