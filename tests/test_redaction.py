"""Stored technical text never contains credentials, tokens or connection strings."""

import pytest

from src.services.redaction import MAX_STORED_CHARS, REDACTED, sanitize

BEARER = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzdmMifQ.c2lnbmF0dXJl"
SECRETS = {
    "Authorization: Bearer abcdefgh12345678": "abcdefgh12345678",
    f"bearer {BEARER}": BEARER,
    "Authorization: Basic dXNlcjpwYXNzd29yZA==": "dXNlcjpwYXNzd29yZA==",
    "postgresql+asyncpg://svc:S3cr3t!@db01:5432/aio": "S3cr3t!",
    "sqlite+aiosqlite:///C:/data/aio_prod.db": "aio_prod.db",
    "Server=sql01;Database=bc;User ID=sa;Password=Hunter2;": "Hunter2",
    "client_secret=Q~abc.def-123": "Q~abc.def-123",
    '{"access_token": "tok-987654"}': "tok-987654",
    "X-API-Key: frontu-key-42": "frontu-key-42",
    "https://user:pa55word@api.example.test/v1": "pa55word",
    "password=letmein&user=x": "letmein",
}


@pytest.mark.parametrize(("text", "secret"), SECRETS.items())
def test_secret_is_removed(text: str, secret: str) -> None:
    cleaned = sanitize(f"frontu request failed (status=401): {text} at step 2")

    assert cleaned is not None
    assert secret not in cleaned
    assert REDACTED in cleaned
    assert cleaned.startswith("frontu request failed (status=401): ")


def test_ordinary_vendor_text_is_kept() -> None:
    text = "business-central request failed (status=400): Field 'No' must not be blank"

    assert sanitize(text) == text


def test_long_text_is_truncated_after_redaction() -> None:
    cleaned = sanitize("Bearer " + "a" * 40 + " " + "x" * 2000)

    assert cleaned is not None
    assert len(cleaned) == MAX_STORED_CHARS
    assert cleaned.startswith(f"Bearer {REDACTED} ") and cleaned.endswith("…")


def test_none_stays_none() -> None:
    assert sanitize(None) is None
