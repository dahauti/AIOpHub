-- Operations Center, phase 1 (ADR 0003): process registry, job runs, per-record outcomes.
-- Portable SQL (SQLite now, PostgreSQL 14 later). Stored values are English ASCII codes;
-- LT/EN text lives only in src/services/error_catalog.py.

CREATE TABLE IF NOT EXISTS processes (
    id TEXT PRIMARY KEY,
    name_key TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('bc_to_frontu', 'frontu_to_bc', 'both')),
    owner TEXT
);

INSERT INTO processes (id, name_key, direction) VALUES
    ('workspaces_vehicles', 'process.workspaces_vehicles', 'bc_to_frontu'),
    ('tasks_sync', 'process.tasks_sync', 'both')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS job_runs (
    run_id TEXT PRIMARY KEY,
    process_id TEXT NOT NULL REFERENCES processes (id),
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('schedule', 'manual', 'canary')),
    mode TEXT NOT NULL CHECK (mode IN ('DRY_RUN', 'LIVE')),
    profile TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'succeeded', 'failed', 'aborted', 'skipped')),
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITH TIME ZONE,
    count_ok INTEGER NOT NULL DEFAULT 0,
    count_skipped INTEGER NOT NULL DEFAULT 0,
    count_failed INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_summary TEXT,
    trace_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_runs_process_started ON job_runs (process_id, started_at);

CREATE TABLE IF NOT EXISTS record_outcomes (
    outcome_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES job_runs (run_id),
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('ok', 'skipped', 'error')),
    error_code TEXT,
    error_type TEXT CHECK (error_type IN ('data', 'system')),
    params TEXT,
    technical_detail TEXT,
    http_status INTEGER,
    trace_id TEXT NOT NULL,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
    UNIQUE (run_id, object_type, object_id)
);

CREATE INDEX IF NOT EXISTS idx_record_outcomes_object ON record_outcomes (object_id);
CREATE INDEX IF NOT EXISTS idx_record_outcomes_error_code ON record_outcomes (error_code);
