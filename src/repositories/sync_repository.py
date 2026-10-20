"""Sync state, fingerprint and audit persistence (SQLAlchemy 2.0 async, Postgres)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Executable

from src.repositories import sync_statements as sql
from src.schemas.sync import AuditEntry, AuditRecord, DashboardSnapshot, ReleaseStatus


def _total(counts: dict[str, int], *actions: str) -> int:
    return sum(counts.get(action, 0) for action in actions)


class SyncRepository(Protocol):
    """Storage port used by the pipeline and dashboard; bound to one module."""

    async def try_acquire(self, now: datetime, initial_cursor: datetime) -> bool: ...
    async def heartbeat(self, now: datetime) -> None: ...
    async def release(self, status: ReleaseStatus, now: datetime) -> None: ...
    async def get_cursor(self) -> datetime: ...
    async def advance_cursor(self, cursor: datetime, now: datetime) -> None: ...
    async def get_fingerprints(self, keys: Sequence[str]) -> dict[str, str]: ...
    async def save_fingerprint(self, key: str, payload_hash: str, now: datetime) -> None: ...
    async def delete_fingerprint(self, key: str) -> None: ...
    async def record_audit(self, entry: AuditEntry) -> None: ...
    async def dashboard_snapshot(self, since: datetime, limit: int) -> DashboardSnapshot: ...


class SqlSyncRepository:
    """Postgres implementation; every call runs in its own short transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], module: str) -> None:
        self._sessions = sessions
        self._module = module

    async def try_acquire(self, now: datetime, initial_cursor: datetime) -> bool:
        async with self._sessions.begin() as session:
            claimed = await session.scalar(sql.claim_state(self._module, now, initial_cursor))
        return claimed is not None

    async def heartbeat(self, now: datetime) -> None:
        await self._execute(sql.heartbeat_state(self._module, now))

    async def release(self, status: ReleaseStatus, now: datetime) -> None:
        await self._execute(sql.release_state(self._module, status, now))

    async def get_cursor(self) -> datetime:
        async with self._sessions() as session:
            state = await session.scalar(sql.select_state(self._module))
        if state is None:
            raise RuntimeError(f"No sync state for {self._module!r}; acquire the module first")
        return state.cursor_timestamp

    async def advance_cursor(self, cursor: datetime, now: datetime) -> None:
        await self._execute(sql.advance_cursor(self._module, cursor, now))

    async def get_fingerprints(self, keys: Sequence[str]) -> dict[str, str]:
        if not keys:
            return {}
        async with self._sessions() as session:
            result = await session.execute(sql.select_fingerprints(self._module, keys))
            return {key: payload_hash for key, payload_hash in result}

    async def save_fingerprint(self, key: str, payload_hash: str, now: datetime) -> None:
        await self._execute(sql.upsert_fingerprint(self._module, key, payload_hash, now))

    async def delete_fingerprint(self, key: str) -> None:
        await self._execute(sql.delete_fingerprint(self._module, key))

    async def record_audit(self, entry: AuditEntry) -> None:
        await self._execute(sql.insert_audit(entry))

    async def dashboard_snapshot(self, since: datetime, limit: int) -> DashboardSnapshot:
        async with self._sessions() as session:
            state = await session.scalar(sql.select_state(self._module))
            rows = await session.execute(sql.count_actions_since(since))
            counts = {action: total for action, total in rows}
            recent = (await session.scalars(sql.recent_audit(limit))).all()
        return DashboardSnapshot(
            status=state.status if state is not None else "IDLE",
            cursor_timestamp=state.cursor_timestamp if state is not None else None,
            updated_at=state.updated_at if state is not None else None,
            processed=_total(counts, "UPSERT", "ARCHIVE", "SINGLE_UPSERT", "SINGLE_ARCHIVE"),
            skipped=counts.get("SKIPPED", 0),
            errors=counts.get("FAILED", 0),
            planned=_total(
                counts,
                "DRY_RUN_UPSERT",
                "DRY_RUN_ARCHIVE",
                "DRY_RUN_SINGLE_UPSERT",
                "DRY_RUN_SINGLE_ARCHIVE",
            ),
            recent=[AuditRecord.model_validate(row) for row in recent],
        )

    async def _execute(self, statement: Executable) -> None:
        async with self._sessions.begin() as session:
            await session.execute(statement)
