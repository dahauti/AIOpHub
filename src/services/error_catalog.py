"""Error catalog: language-neutral code -> type (data / system) + LT/EN business sentence.

Codes are stored in `record_outcomes` / `job_runs`; text is rendered only from here (ADR 0002
7 p., ADR 0003 4 p.). An unknown code or exception is a system error. Codes come from the
current sync code paths; new codes need text in every language (tests/test_error_catalog.py).
"""

import string
from typing import Literal

from pydantic import BaseModel

from src.adapters.resilience import ExternalServiceError
from src.schemas.operations import ErrorType

Language = Literal["lt", "en"]
UNKNOWN = "system.unknown"


class ErrorDefinition(BaseModel):
    error_type: ErrorType
    text: dict[Language, str]


class ClassifiedError(BaseModel):
    code: str
    http_status: int | None = None
    detail: str


CATALOG: dict[str, ErrorDefinition] = {
    "task.no_order_no": ErrorDefinition(
        error_type="data",
        text={
            "lt": "BC užsakymas neturi numerio.",
            "en": "The BC order has no number.",
        },
    ),
    "task.no_client_match": ErrorDefinition(
        error_type="data",
        text={
            "lt": "BC klientas {customer_no} nerastas Frontu.",
            "en": "BC customer {customer_no} was not found in Frontu.",
        },
    ),
    "task.order_not_found": ErrorDefinition(
        error_type="data",
        text={
            "lt": "BC užsakymas {order_no} nerastas.",
            "en": "BC order {order_no} was not found.",
        },
    ),
    "task.not_frontu_status": ErrorDefinition(
        error_type="data",
        text={
            "lt": "BC užsakymo būsena „{status}“, ne FRONTU, todėl jis nesiunčiamas.",
            "en": "The BC order status is “{status}”, not FRONTU, so it is not sent.",
        },
    ),
    "system.bc_error": ErrorDefinition(
        error_type="system",
        text={
            "lt": "Business Central neatsakė arba grąžino klaidą (HTTP {http_status}).",
            "en": "Business Central did not respond or returned an error (HTTP {http_status}).",
        },
    ),
    "system.frontu_error": ErrorDefinition(
        error_type="system",
        text={
            "lt": "Frontu neatsakė arba grąžino klaidą (HTTP {http_status}).",
            "en": "Frontu did not respond or returned an error (HTTP {http_status}).",
        },
    ),
    "system.bc_unreachable": ErrorDefinition(
        error_type="system",
        text={
            "lt": "Nepavyko prisijungti prie BC \u2013 patikrinkite tinklo ryšį arba VPN.",
            "en": "Could not connect to BC \u2013 check the network connection or VPN.",
        },
    ),
    "system.frontu_unreachable": ErrorDefinition(
        error_type="system",
        text={
            "lt": "Nepavyko prisijungti prie Frontu \u2013 patikrinkite tinklo ryšį.",
            "en": "Could not connect to Frontu \u2013 check the network connection.",
        },
    ),
    "system.auth": ErrorDefinition(
        error_type="system",
        text={
            "lt": "Nepavyko prisijungti prie Business Central (Entra ID).",
            "en": "Signing in to Business Central (Entra ID) failed.",
        },
    ),
    UNKNOWN: ErrorDefinition(
        error_type="system",
        text={
            "lt": "Nenumatyta sistemos klaida. Perduokite trace ID administratoriui.",
            "en": "Unexpected system error. Give the trace ID to an administrator.",
        },
    ),
}

# ExternalServiceError.service -> code (service names from the adapters' SERVICE constants)
SERVICE_CODES: dict[str, str] = {
    "business-central": "system.bc_error",
    "frontu": "system.frontu_error",
    "entra-id": "system.auth",
}
# The same services when no HTTP response came back (ExternalServiceError.unreachable)
UNREACHABLE_CODES: dict[str, str] = {
    "business-central": "system.bc_unreachable",
    "frontu": "system.frontu_unreachable",
}


def error_type(code: str) -> ErrorType:
    return CATALOG.get(code, CATALOG[UNKNOWN]).error_type


def classify(error: BaseException) -> ClassifiedError:
    """Code, HTTP status and raw (unsanitised) detail of an exception."""
    if isinstance(error, ExternalServiceError):
        codes = UNREACHABLE_CODES if error.unreachable else {}
        code = codes.get(error.service) or SERVICE_CODES.get(error.service, UNKNOWN)
        return ClassifiedError(code=code, http_status=error.status_code, detail=str(error))
    return ClassifiedError(code=UNKNOWN, detail=f"{type(error).__name__}: {error}")


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def message(code: str, language: Language, params: dict[str, str] | None = None) -> str:
    """Business sentence; a missing parameter renders as `?`, an unknown code as UNKNOWN."""
    template = CATALOG.get(code, CATALOG[UNKNOWN]).text[language]
    values = params or {}
    return template.format_map({name: values.get(name, "?") for name in placeholders(template)})
