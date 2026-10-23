"""Shared HTTP resilience: tenacity retries on 429/502/503/504 and domain error mapping."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.adapters.link_health import LINK_HEALTH

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
MAX_ATTEMPTS = 5
MAX_ERROR_DETAIL_CHARS = 500
_backoff = wait_exponential_jitter(initial=1, max=30)

Sleep = Callable[[float], Awaitable[None]]
Hook = Callable[[], Awaitable[None]]


class ExternalServiceError(Exception):
    """A third-party call failed permanently: non-retryable status, exhausted retries or I/O."""

    def __init__(
        self,
        service: str,
        status_code: int | None,
        detail: str,
        *,
        url: str | None = None,
        unreachable: bool = False,
        unexpected_response: bool = False,
    ) -> None:
        super().__init__(f"{service} request failed (status={status_code}): {detail}")
        self.service = service
        self.status_code = status_code
        self.detail = detail
        self.url = url
        self.unreachable = unreachable  # no HTTP response at all: network, DNS, TLS, timeout
        self.unexpected_response = unexpected_response  # success status, unrecognized body


class _RetryableStatusError(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.status_code = response.status_code
        self.retry_after = parse_retry_after(response.headers.get("Retry-After"))


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header given as delta-seconds or an HTTP date."""
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


def _wait(state: RetryCallState) -> float:
    error = state.outcome.exception() if state.outcome is not None else None
    if isinstance(error, _RetryableStatusError) and error.retry_after is not None:
        return error.retry_after
    return _backoff(state)


def _log_retry(service: str, request: httpx.Request) -> Callable[[RetryCallState], None]:
    def log(state: RetryCallState) -> None:
        wait = state.next_action.sleep if state.next_action is not None else None
        logger.warning(
            "Retrying %s %s",
            request.method,
            request.url.path,
            extra={"service": service, "attempt": state.attempt_number, "wait_seconds": wait},
        )

    return log


async def send_with_retry(
    client: httpx.AsyncClient,
    request: httpx.Request,
    *,
    service: str,
    sleep: Sleep = asyncio.sleep,
    before_attempt: Hook | None = None,
) -> httpx.Response:
    """Send `request`, retrying only RETRYABLE_STATUS_CODES; map every failure to a domain error."""
    retrying = AsyncRetrying(
        retry=retry_if_exception_type(_RetryableStatusError),
        wait=_wait,
        stop=stop_after_attempt(MAX_ATTEMPTS),
        sleep=sleep,
        before_sleep=_log_retry(service, request),
        reraise=True,
    )
    url = str(request.url)
    try:
        async for attempt in retrying:
            with attempt:
                if before_attempt is not None:
                    await before_attempt()
                response = await client.send(request)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    raise _RetryableStatusError(response)
    except _RetryableStatusError as exc:
        detail = f"gave up after {MAX_ATTEMPTS} attempts"
        LINK_HEALTH.record(service, exc.status_code, detail)
        raise ExternalServiceError(service, exc.status_code, detail, url=url) from exc
    except httpx.TransportError as exc:
        detail = f"{type(exc).__name__}: {exc}"
        LINK_HEALTH.record(service, None, detail)
        raise ExternalServiceError(service, None, detail, url=url, unreachable=True) from exc
    if response.is_error:
        detail = _error_detail(service, response)
        LINK_HEALTH.record(service, response.status_code, detail)
        raise ExternalServiceError(service, response.status_code, detail, url=url)
    LINK_HEALTH.record(service, response.status_code)
    return response


def _error_detail(service: str, response: httpx.Response) -> str:
    """Keep JSON error bodies (vendor messages); never store an HTML or plain-text page."""
    if not response.headers.get("content-type", "").startswith("text/html"):
        try:
            json.loads(response.content)
        except ValueError:
            pass
        else:
            return response.text[:MAX_ERROR_DETAIL_CHARS]
    return f"{service} returned HTTP {response.status_code} (non-JSON response)"
