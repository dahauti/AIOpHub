"""Model metadata matches SPEC section 4 tables on SQLite (flat namespace, JSON payload)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import JSON, Table, make_url
from sqlalchemy.schema import CreateTable

from src.models.audit import (
    Base,
    SyncAuditLog,
    SyncConfig,
    SyncFingerprint,
    SyncState,
    UtcDateTime,
)

SQLITE = make_url("sqlite://").get_dialect()()


def _table(model: type[Base]) -> Table:
    table = model.__table__
    assert isinstance(table, Table)
    return table


def _ddl(table: Table) -> str:
    return " ".join(str(CreateTable(table).compile(dialect=SQLITE)).split())


def test_tables_use_flat_namespace() -> None:
    assert {t.fullname for t in Base.metadata.sorted_tables} == {
        "sync_states",
        "sync_fingerprints",
        "sync_audit_log",
        "sync_config",
        "link_health",
    }
    assert all(t.schema is None for t in Base.metadata.sorted_tables)


def test_sync_states_matches_spec() -> None:
    table = _table(SyncState)

    assert [c.name for c in table.primary_key] == ["module_name"]
    assert all(not table.c[n].nullable for n in ("cursor_timestamp", "status", "updated_at"))
    ddl = _ddl(table)
    assert "module_name VARCHAR(64) NOT NULL" in ddl
    assert "status VARCHAR(32) DEFAULT 'IDLE' NOT NULL" in ddl
    assert "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL" in ddl


def test_sync_config_defaults_to_dry_run_with_auto_sync_off() -> None:
    table = _table(SyncConfig)

    assert [c.name for c in table.primary_key] == ["module_name"]
    ddl = _ddl(table)
    assert "dry_run BOOLEAN DEFAULT 1 NOT NULL" in ddl
    assert "auto_sync_enabled BOOLEAN DEFAULT 0 NOT NULL" in ddl
    assert "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL" in ddl


def test_sync_fingerprints_matches_spec() -> None:
    table = _table(SyncFingerprint)

    assert [c.name for c in table.primary_key] == ["module_name", "entity_key"]
    ddl = _ddl(table)
    assert "entity_key VARCHAR(128) NOT NULL" in ddl
    assert "payload_hash VARCHAR(64) NOT NULL" in ddl
    assert "PRIMARY KEY (module_name, entity_key)" in ddl


def test_sync_audit_log_matches_spec() -> None:
    table = _table(SyncAuditLog)
    cols = table.c

    assert [c.name for c in table.primary_key] == ["id"]
    for name in ("status_code", "payload", "error_message"):
        assert cols[name].nullable is True, name
    assert isinstance(cols.payload.type, JSON)
    ddl = _ddl(table)
    assert "id INTEGER NOT NULL" in ddl
    assert "PRIMARY KEY (id)" in ddl
    assert "action VARCHAR(32) NOT NULL" in ddl
    assert "payload JSON," in ddl
    assert "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL" in ddl
    assert "UNIQUE" not in ddl


def test_sync_audit_log_index_matches_spec() -> None:
    indexes = {ix.name: [c.name for c in ix.columns] for ix in _table(SyncAuditLog).indexes}

    assert indexes == {"idx_audit_entity": ["entity_key"], "idx_audit_created_at": ["created_at"]}


def test_utc_datetime_normalises_to_utc_and_rejects_naive() -> None:
    column_type = UtcDateTime()
    local = datetime(2026, 10, 20, 15, 0, tzinfo=timezone(timedelta(hours=3)))

    stored = column_type.process_bind_param(local, SQLITE)
    assert stored == datetime(2026, 10, 20, 12, 0)
    assert column_type.process_result_value(stored, SQLITE) == datetime(
        2026, 10, 20, 12, 0, tzinfo=UTC
    )
    assert column_type.process_bind_param(None, SQLITE) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        column_type.process_bind_param(datetime(2026, 10, 20, 12, 0), SQLITE)
