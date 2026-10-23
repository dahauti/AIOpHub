"""Error catalog: English ASCII codes, text in every language, unknown -> system."""

import re
from collections.abc import Callable
from typing import get_args

import httpx
import pytest

from src.adapters.resilience import ExternalServiceError, send_with_retry
from src.clients.frontu_task_payloads import FrontuRejectedError
from src.services.error_catalog import (
    CATALOG,
    UNKNOWN,
    Language,
    classify,
    error_type,
    message,
    placeholders,
    rejection_params,
)

LANGUAGES = get_args(Language)
EXPECTED_CODES = {
    "task.no_order_no",
    "task.no_client_match",
    "task.frontu_rejected",
    "task.task_type_not_found",
    "task.order_not_found",
    "task.not_frontu_status",
    "system.bc_error",
    "system.frontu_error",
    "system.bc_unreachable",
    "system.frontu_unreachable",
    "system.frontu_unexpected_response",
    "system.auth",
    "system.unknown",
}


def test_catalog_has_exactly_the_phase_1_codes() -> None:
    assert set(CATALOG) == EXPECTED_CODES


@pytest.mark.parametrize("code", sorted(EXPECTED_CODES))
def test_codes_are_language_neutral_ascii(code: str) -> None:
    assert re.fullmatch(r"[a-z]+\.[a-z_]+", code)


@pytest.mark.parametrize("code", sorted(EXPECTED_CODES))
def test_every_code_has_text_in_every_language_with_the_same_placeholders(code: str) -> None:
    texts = CATALOG[code].text

    assert set(texts) == set(LANGUAGES)
    assert all(texts[lang].strip() for lang in LANGUAGES)
    assert len({frozenset(placeholders(texts[lang])) for lang in LANGUAGES}) == 1


def test_unknown_code_is_a_system_error_with_the_unknown_text() -> None:
    assert error_type("vendor.never_seen") == "system"
    assert message("vendor.never_seen", "en") == CATALOG[UNKNOWN].text["en"]


def test_message_fills_parameters_and_marks_missing_ones() -> None:
    assert message("task.no_client_match", "lt", {"customer_no": "C00010"}) == (
        "BC klientas C00010 nerastas Frontu."
    )
    assert message("task.order_not_found", "en") == "BC order ? was not found."


@pytest.mark.parametrize(
    ("service", "code"),
    [
        ("business-central", "system.bc_error"),
        ("frontu", "system.frontu_error"),
        ("entra-id", "system.auth"),
        ("pipedrive", UNKNOWN),
    ],
)
def test_external_errors_are_classified_by_service(service: str, code: str) -> None:
    classified = classify(ExternalServiceError(service, 503, "unavailable"))

    assert (classified.code, classified.http_status) == (code, 503)
    assert error_type(classified.code) == "system"
    assert "unavailable" in classified.detail


def test_frontu_success_with_unrecognized_body_is_unexpected_response() -> None:
    error = ExternalServiceError(
        "frontu", 200, "unexpected listing payload", unexpected_response=True
    )

    classified = classify(error)

    assert (classified.code, classified.http_status) == ("system.frontu_unexpected_response", 200)
    assert error_type(classified.code) == "system"
    assert message(classified.code, "lt") == (
        "Frontu atsakė netikėtu formatu \u2013 reikia patikrinti integraciją."
    )


def test_frontu_write_rejection_is_a_data_error_showing_frontus_reason() -> None:
    raw = '{"error": "title required"}'
    rejected = FrontuRejectedError(ExternalServiceError("frontu", 422, raw))

    classified = classify(rejected)

    assert (classified.code, classified.http_status) == ("task.frontu_rejected", 422)
    assert error_type(classified.code) == "data"
    params = rejection_params(rejected)
    assert message(classified.code, "en", params) == "Frontu rejected the record: title required"


async def _send_error(
    service: str, handler: Callable[[httpx.Request], httpx.Response]
) -> ExternalServiceError:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        request = http.build_request("GET", "https://example.test/x")
        with pytest.raises(ExternalServiceError) as caught:
            await send_with_retry(http, request, service=service)
    return caught.value


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("All connection attempts failed", request=request)


def _reject(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"error": "bad filter"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "handler", "code", "http_status"),
    [
        ("business-central", _refuse, "system.bc_unreachable", None),
        ("frontu", _refuse, "system.frontu_unreachable", None),
        ("business-central", _reject, "system.bc_error", 400),
        ("frontu", _reject, "system.frontu_error", 400),
    ],
)
async def test_network_failure_is_unreachable_and_http_error_is_error(
    service: str,
    handler: Callable[[httpx.Request], httpx.Response],
    code: str,
    http_status: int | None,
) -> None:
    classified = classify(await _send_error(service, handler))

    assert (classified.code, classified.http_status) == (code, http_status)
    assert error_type(classified.code) == "system"


def test_unreachable_bc_text_points_to_network_or_vpn() -> None:
    assert message("system.bc_unreachable", "lt") == (
        "Nepavyko prisijungti prie BC \u2013 patikrinkite tinklo ryšį arba VPN."
    )
    assert "VPN" in message("system.bc_unreachable", "en")


def test_any_other_exception_is_unknown_system() -> None:
    classified = classify(KeyError("x"))

    assert classified.code == UNKNOWN and classified.http_status is None
    assert classified.detail.startswith("KeyError")
