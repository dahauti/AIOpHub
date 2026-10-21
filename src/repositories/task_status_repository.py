"""Read-only cockpit queries for the task stream: `sync_states` row and latest TASK_RUN entry."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.audit import SyncAuditLog, SyncState


class StreamState(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    updated_at: datetime


class RunEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    created_at: datetime
    payload: dict[str, JsonValue]


class TaskStatusRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], module: str) -> None:
        self._sessions = sessions
        self._module = module

    async def stream_state(self) -> StreamState | None:
        """The module's lock row; None before its first run."""
        async with self._sessions() as session:
            row = await session.get(SyncState, self._module)
        return None if row is None else StreamState(status=row.status, updated_at=row.updated_at)

    async def latest_run(self) -> RunEntry | None:
        """Newest `TASK_RUN` audit entry for the module, or None."""
        statement = (
            select(SyncAuditLog)
            .where(SyncAuditLog.action == "TASK_RUN", SyncAuditLog.entity_key == self._module)
            .order_by(SyncAuditLog.created_at.desc(), SyncAuditLog.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
        if row is None or row.payload is None:
            return None
        return RunEntry(created_at=row.created_at, payload=row.payload)
