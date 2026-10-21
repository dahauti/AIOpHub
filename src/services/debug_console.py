"""Diagnostic event viewer: audit rows classified by level and stream, with a raw JSON drawer."""

import json
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, JsonValue

from src.repositories.event_repository import AuditEvent
from src.schemas.audit_filter import ACTION_GROUPS
from src.services.task_runner import LOCK_MODULE

Level = Literal["INFO", "WARN", "ERROR"]
Stream = Literal["Tasks", "Vehicles"]
CONSOLE_LIMIT = 60
TASK_ACTIONS = frozenset(ACTION_GROUPS["TASKS"])


class DebugEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    created_at: datetime
    level: Level
    stream: Stream
    message: str
    raw_json: str


def _level(event: AuditEvent) -> Level:
    code = event.status_code or 0
    if event.action == "FAILED" or code >= 500:
        return "ERROR"
    if event.action == "ABORTED" or code >= 400 or event.error_message:
        return "WARN"
    return "INFO"


def _message(event: AuditEvent) -> str:
    operation = (event.payload or {}).get("operation")
    parts = [event.action, event.entity_key]
    if isinstance(operation, str):
        parts.append(operation)
    if event.status_code is not None:
        parts.append(f"HTTP {event.status_code}")
    text = " · ".join(parts)
    return f"{text}: {event.error_message}" if event.error_message else text


def to_debug_event(event: AuditEvent) -> DebugEvent:
    """Request = the stored payload; response = what the audit row kept of the reply."""
    tasks = event.action in TASK_ACTIONS or event.entity_key == LOCK_MODULE
    response: dict[str, JsonValue] = {
        "status_code": event.status_code,
        "error": event.error_message,
    }
    raw: dict[str, JsonValue] = {"request": event.payload, "response": response}
    return DebugEvent(
        id=event.id,
        created_at=event.created_at,
        level=_level(event),
        stream="Tasks" if tasks else "Vehicles",
        message=_message(event),
        raw_json=json.dumps(raw, indent=2, ensure_ascii=False),
    )


class EventSource(Protocol):
    async def recent(self, limit: int) -> list[AuditEvent]: ...


@runtime_checkable
class DebugConsolePort(Protocol):
    async def events(self) -> list[DebugEvent]: ...


class DebugConsole:
    def __init__(self, source: EventSource, limit: int = CONSOLE_LIMIT) -> None:
        self._source = source
        self._limit = limit

    async def events(self) -> list[DebugEvent]:
        return [to_debug_event(event) for event in await self._source.recent(self._limit)]
