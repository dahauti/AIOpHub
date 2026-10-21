"""Read-only `sync_audit_log` access with raw payloads, for the Glass Box console and canary."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.audit import SyncAuditLog
from src.repositories import sync_statements as sql


class AuditEvent(BaseModel):
    """One audit row including its stored request payload."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    created_at: datetime
    entity_key: str
    action: str
    status_code: int | None = None
    payload: dict[str, JsonValue] | None = None
    error_message: str | None = None


class AuditEventRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def recent(self, limit: int) -> list[AuditEvent]:
        """Newest rows first, capped at `limit`."""
        async with self._sessions() as session:
            rows = (await session.scalars(sql.recent_audit(limit))).all()
        return [AuditEvent.model_validate(row) for row in rows]

    async def since(self, when: datetime, action: str) -> list[AuditEvent]:
        """Rows of one action written at or after `when`, oldest first."""
        statement = (
            select(SyncAuditLog)
            .where(SyncAuditLog.created_at >= when, SyncAuditLog.action == action)
            .order_by(SyncAuditLog.created_at, SyncAuditLog.id)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [AuditEvent.model_validate(row) for row in rows]
