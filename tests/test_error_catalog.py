"""Error catalog: English ASCII codes, text in every language, unknown -> system."""

import re
from typing import get_args

import pytest

from src.adapters.resilience import ExternalServiceError
from src.services.error_catalog import (
    CATALOG,
    UNKNOWN,
    Language,
    classify,
    error_type,
    message,
    placeholders,
)

LANGUAGES = get_args(Language)
EXPECTED_CODES = {
    "task.no_order_no",
    "task.no_client_match",
    "task.order_not_found",
    "task.not_frontu_status",
    "system.bc_error",
    "system.frontu_error",
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


def test_any_other_exception_is_unknown_system() -> None:
    classified = classify(KeyError("x"))

    assert classified.code == UNKNOWN and classified.http_status is None
    assert classified.detail.startswith("KeyError")
