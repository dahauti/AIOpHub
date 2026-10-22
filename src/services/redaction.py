"""Sanitise technical error text before it is stored (ADR 0003, CLAUDE.md §4).

Removes Authorization/Bearer/Basic credentials, JWTs, `key=value` secrets, credentials in
URLs and whole database connection strings, then truncates. Applied to every
`technical_detail` and `error_summary`; vendor messages are otherwise kept for diagnosis.
"""

import re

MAX_STORED_CHARS = 500
REDACTED = "[REDACTED]"

_SECRET_NAME = (
    r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|client[_-]?secret|private[_-]?key|signature|sig|credential)[\w.-]*"
)
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Database URLs (with or without credentials) are connection strings: drop them whole.
    (
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mariadb|mssql|sqlserver|oracle|sqlite|mongodb"
            r"(?:\+srv)?|redis|amqp)(?:\+\w+)?://\S+",
            re.I,
        ),
        REDACTED,
    ),
    # ADO/ODBC style: Server=...;User ID=...;Password=...; -> drop the whole string.
    (
        re.compile(
            r"(?:\b(?:server|data source|host|address)\s*=[^;\n]*;)(?:[^;\n]*;)*?"
            r"[^;\n]*\b(?:password|pwd)\s*=[^;\n]*;?",
            re.I,
        ),
        REDACTED,
    ),
    (
        re.compile(r"(?i)\b((?:proxy-)?authorization)(\"?\s*[:=]\s*\"?)[^\r\n\",}]+"),
        rf"\1\2{REDACTED}",
    ),
    (
        re.compile(r"(?i)\b(bearer|basic|negotiate|ntlm)\s+[A-Za-z0-9._~+/=-]{8,}"),
        rf"\1 {REDACTED}",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), REDACTED),
    (re.compile(rf"(?i)\b({_SECRET_NAME})(\"?\s*[:=]\s*\"?)[^\s\"'&;,}}]+"), rf"\1\2{REDACTED}"),
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s/:@]+):[^\s/@]+@"), rf"\1:{REDACTED}@"),
)


def sanitize(text: str | None) -> str | None:
    """Text safe to store, at most `MAX_STORED_CHARS`; None stays None."""
    if text is None:
        return None
    cleaned = text
    for pattern, replacement in _RULES:
        cleaned = pattern.sub(replacement, cleaned)
    if len(cleaned) > MAX_STORED_CHARS:
        cleaned = cleaned[: MAX_STORED_CHARS - 1] + "…"
    return cleaned
