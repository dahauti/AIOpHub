"""Model metadata must match the sync_engine DDL in SPEC section 4."""

from sqlalchemy import Table, make_url
from sqlalchemy.schema import CreateTable

from src.models.audit import Base, SyncAuditLog, SyncFingerprint, SyncState

PG_DIALECT = make_url("postgresql://").get_dialect()()


def _table(model: type[Base]) -> Table:
    table = model.__table__
    assert isinstance(table, Table)
    return table


def _ddl(table: Table) -> str:
    return str(CreateTable(table).compile(dialect=PG_DIALECT))


def test_all_tables_live_in_sync_engine_schema() -> None:
    assert {t.fullname for t in Base.metadata.sorted_tables} == {
        "sync_engine.sync_states",
        "sync_engine.sync_fingerprints",
        "sync_engine.sync_audit_log",
    }


def test_sync_states_matches_spec() -> None:
    table = _table(SyncState)

    assert [c.name for c in table.primary_key] == ["module_name"]
    ddl = _ddl(table)
    assert "module_name VARCHAR(64) NOT NULL" in ddl
    assert "cursor_timestamp TIMESTAMP WITH TIME ZONE NOT NULL" in ddl
    assert "status VARCHAR(32) DEFAULT 'IDLE' NOT NULL" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl


def test_sync_fingerprints_matches_spec() -> None:
    table = _table(SyncFingerprint)

    assert [c.name for c in table.primary_key] == ["module_name", "entity_key"]
    ddl = _ddl(table)
    assert "module_name VARCHAR(64) NOT NULL" in ddl
    assert "entity_key VARCHAR(128) NOT NULL" in ddl
    assert "payload_hash VARCHAR(64) NOT NULL" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "PRIMARY KEY (module_name, entity_key)" in ddl


def test_sync_audit_log_matches_spec() -> None:
    table = _table(SyncAuditLog)
    cols = table.c

    assert [c.name for c in table.primary_key] == ["id"]
    for name in ("status_code", "payload", "error_message"):
        assert cols[name].nullable is True, name
    ddl = _ddl(table)
    assert "id BIGSERIAL NOT NULL" in ddl
    assert "entity_key VARCHAR(128) NOT NULL" in ddl
    assert "action VARCHAR(32) NOT NULL" in ddl
    assert "status_code INTEGER," in ddl
    assert "payload JSONB," in ddl
    assert "error_message TEXT," in ddl
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "UNIQUE" not in ddl


def test_sync_audit_log_index_matches_spec() -> None:
    indexes = {ix.name: [c.name for c in ix.columns] for ix in _table(SyncAuditLog).indexes}

    assert indexes == {"idx_audit_entity": ["entity_key"]}
