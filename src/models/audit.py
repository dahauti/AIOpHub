"""Sync state, fingerprint and audit entities (SPEC section 4) on embedded SQLite."""

from datetime import UTC, datetime

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Dialect,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Stores UTC as naive text (SQLite has no tz) and returns timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UtcDateTime requires timezone-aware datetimes")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


# SQLite only auto-increments an INTEGER PRIMARY KEY (rowid alias), not BIGINT.
AUDIT_ID = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    """Declarative base for all sync engine entities."""


class SyncState(Base):
    """Per-module high-water-mark cursor and execution status."""

    __tablename__ = "sync_states"

    module_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor_timestamp: Mapped[datetime] = mapped_column(UtcDateTime())
    status: Mapped[str] = mapped_column(String(32), server_default="IDLE")
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), server_default=func.now())


class SyncConfig(Base):
    """Per-module runtime toggles and schedule set from the dashboard; absent row -> defaults."""

    __tablename__ = "sync_config"

    module_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, server_default=true())
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    schedule_mode: Mapped[str] = mapped_column(String(16), server_default="nightly")
    nightly_time: Mapped[str] = mapped_column(String(5), server_default="02:00")
    interval_minutes: Mapped[int] = mapped_column(Integer, server_default="60")
    full_sync_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    full_sync_weekday: Mapped[int] = mapped_column(Integer, server_default="6")
    full_sync_time: Mapped[str] = mapped_column(String(5), server_default="02:00")
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), server_default=func.now())


class SyncFingerprint(Base):
    """Last pushed payload hash per entity, used to skip unchanged records."""

    __tablename__ = "sync_fingerprints"

    module_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), server_default=func.now())


class SyncAuditLog(Base):
    """Audit trail of every outbound sync transaction."""

    __tablename__ = "sync_audit_log"
    __table_args__ = (
        Index("idx_audit_entity", "entity_key"),
        Index("idx_audit_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(AUDIT_ID, primary_key=True, autoincrement=True)
    entity_key: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    status_code: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), server_default=func.now())


class LinkHealth(Base):
    """Latest HTTP outcome per external service, persisted by each run for the dashboard."""

    __tablename__ = "link_health"

    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    status_code: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(UtcDateTime())
