"""SQLAlchemy statement builders for the sync_engine tables (pure, no I/O)."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Delete, Select, Update, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.dml import Insert, ReturningInsert

from src.models.audit import SyncAuditLog, SyncFingerprint, SyncState
from src.schemas.sync import RUNNING, AuditEntry, ReleaseStatus

STALE_RUN_AFTER = timedelta(hours=2)


def claim_state(
    module: str, now: datetime, initial_cursor: datetime
) -> ReturningInsert[tuple[str]]:
    """Atomically mark the module RUNNING unless a fresh run already holds it."""
    statement = insert(SyncState).values(
        module_name=module, cursor_timestamp=initial_cursor, status=RUNNING, updated_at=now
    )
    return statement.on_conflict_do_update(
        index_elements=[SyncState.module_name],
        set_={"status": RUNNING, "updated_at": now},
        where=or_(SyncState.status != RUNNING, SyncState.updated_at < now - STALE_RUN_AFTER),
    ).returning(SyncState.module_name)


def heartbeat_state(module: str, now: datetime) -> Update:
    return update(SyncState).where(SyncState.module_name == module).values(updated_at=now)


def release_state(module: str, status: ReleaseStatus, now: datetime) -> Update:
    return (
        update(SyncState)
        .where(SyncState.module_name == module)
        .values(status=status, updated_at=now)
    )


def select_state(module: str) -> Select[SyncState]:
    return select(SyncState).where(SyncState.module_name == module)


def advance_cursor(module: str, cursor: datetime, now: datetime) -> Update:
    """Move the cursor forward only; never rewinds an already newer cursor."""
    return (
        update(SyncState)
        .where(SyncState.module_name == module, SyncState.cursor_timestamp < cursor)
        .values(cursor_timestamp=cursor, updated_at=now)
    )


def select_fingerprints(module: str, keys: Sequence[str]) -> Select[str, str]:
    return select(SyncFingerprint.entity_key, SyncFingerprint.payload_hash).where(
        SyncFingerprint.module_name == module, SyncFingerprint.entity_key.in_(keys)
    )


def upsert_fingerprint(module: str, key: str, payload_hash: str, now: datetime) -> Insert:
    statement = insert(SyncFingerprint).values(
        module_name=module, entity_key=key, payload_hash=payload_hash, updated_at=now
    )
    return statement.on_conflict_do_update(
        index_elements=[SyncFingerprint.module_name, SyncFingerprint.entity_key],
        set_={"payload_hash": payload_hash, "updated_at": now},
    )


def delete_fingerprint(module: str, key: str) -> Delete:
    return delete(SyncFingerprint).where(
        SyncFingerprint.module_name == module, SyncFingerprint.entity_key == key
    )


def insert_audit(entry: AuditEntry) -> Insert:
    return insert(SyncAuditLog).values(
        entity_key=entry.entity_key,
        action=entry.action,
        status_code=entry.status_code,
        payload=entry.payload,
        error_message=entry.error_message,
    )


def count_actions_since(since: datetime) -> Select[str, int]:
    return (
        select(SyncAuditLog.action, func.count())
        .where(SyncAuditLog.created_at >= since)
        .group_by(SyncAuditLog.action)
    )


def recent_audit(limit: int) -> Select[SyncAuditLog]:
    return (
        select(SyncAuditLog)
        .order_by(SyncAuditLog.created_at.desc(), SyncAuditLog.id.desc())
        .limit(limit)
    )
