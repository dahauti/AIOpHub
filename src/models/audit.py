"""Sync state, fingerprint and audit entities (SPEC section 4, schema `sync_engine`)."""

from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import BigInteger, DateTime, Index, Integer, MetaData, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "sync_engine"


class Base(DeclarativeBase):
    """Declarative base for all sync engine entities."""

    metadata = MetaData(schema=SCHEMA)


class SyncState(Base):
    """Per-module high-water-mark cursor and execution status."""

    __tablename__ = "sync_states"

    module_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), server_default="IDLE")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncFingerprint(Base):
    """Last pushed payload hash per entity, used to skip unchanged records."""

    __tablename__ = "sync_fingerprints"

    module_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncAuditLog(Base):
    """Audit trail of every outbound sync transaction."""

    __tablename__ = "sync_audit_log"
    __table_args__ = (Index("idx_audit_entity", "entity_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_key: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    status_code: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
