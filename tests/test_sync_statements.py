"""Repository SQL contracts, verified by compiling statements with the Postgres dialect."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import make_url
from sqlalchemy.sql import ClauseElement

from src.repositories import sync_statements as sql
from src.schemas.sync import AuditEntry

PG_DIALECT = make_url("postgresql://").get_dialect()()
MODULE = "workspaces_vehicles"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _sql(statement: ClauseElement) -> str:
    return " ".join(str(statement.compile(dialect=PG_DIALECT)).split())


def _params(statement: ClauseElement) -> dict[str, object]:
    return dict(statement.compile(dialect=PG_DIALECT).params)


def test_claim_is_single_atomic_upsert_with_stale_reclaim() -> None:
    statement = sql.claim_state(MODULE, NOW, NOW - timedelta(days=30))
    text = _sql(statement)

    assert text.startswith("INSERT INTO sync_engine.sync_states")
    assert "ON CONFLICT (module_name) DO UPDATE SET status = %(param_1)s" in text
    assert "WHERE sync_engine.sync_states.status != %(status_1)s" in text
    assert "OR sync_engine.sync_states.updated_at < %(updated_at_1)s" in text
    assert text.endswith("RETURNING sync_engine.sync_states.module_name")
    params = _params(statement)
    assert params["status"] == "RUNNING"
    assert params["status_1"] == "RUNNING"
    assert params["updated_at_1"] == NOW - timedelta(hours=2)
    assert params["cursor_timestamp"] == NOW - timedelta(days=30)


def test_release_and_heartbeat_target_module_row() -> None:
    release = sql.release_state(MODULE, "FAILED", NOW)
    heartbeat = sql.heartbeat_state(MODULE, NOW)

    assert _sql(release).startswith("UPDATE sync_engine.sync_states SET status=")
    assert _params(release)["status"] == "FAILED"
    assert "WHERE sync_engine.sync_states.module_name = " in _sql(heartbeat)
    assert "status" not in _params(heartbeat)


def test_advance_cursor_only_moves_forward() -> None:
    statement = sql.advance_cursor(MODULE, NOW, NOW)
    text = _sql(statement)

    assert "SET cursor_timestamp=" in text
    assert "AND sync_engine.sync_states.cursor_timestamp < " in text


def test_fingerprint_upsert_uses_composite_key() -> None:
    text = _sql(sql.upsert_fingerprint(MODULE, "SN-1", "a" * 64, NOW))

    assert text.startswith("INSERT INTO sync_engine.sync_fingerprints")
    assert "ON CONFLICT (module_name, entity_key) DO UPDATE SET payload_hash = " in text


def test_fingerprint_select_and_delete_are_scoped_to_module() -> None:
    select_text = _sql(sql.select_fingerprints(MODULE, ["SN-1", "SN-2"]))
    delete_text = _sql(sql.delete_fingerprint(MODULE, "SN-1"))

    assert "sync_engine.sync_fingerprints.entity_key IN (__[POSTCOMPILE_entity_key_1])" in (
        select_text
    )
    assert delete_text.startswith("DELETE FROM sync_engine.sync_fingerprints WHERE")
    assert "module_name = " in delete_text
    assert "entity_key = " in delete_text


def test_audit_insert_carries_entry_fields() -> None:
    entry = AuditEntry(entity_key="SN-1", action="FAILED", status_code=422, error_message="invalid")
    params = _params(sql.insert_audit(entry))

    assert params["entity_key"] == "SN-1"
    assert params["action"] == "FAILED"
    assert params["status_code"] == 422
    assert params["error_message"] == "invalid"


def test_dashboard_queries() -> None:
    counts = _sql(sql.count_actions_since(NOW))
    recent = _sql(sql.recent_audit(15))

    assert "count(*)" in counts
    assert "WHERE sync_engine.sync_audit_log.created_at >= " in counts
    assert counts.endswith("GROUP BY sync_engine.sync_audit_log.action")
    assert (
        "ORDER BY sync_engine.sync_audit_log.created_at DESC, sync_engine.sync_audit_log.id DESC"
        in recent
    )
    assert _params(sql.recent_audit(15))["param_1"] == 15
