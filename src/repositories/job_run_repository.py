"""SQL access for `job_runs` and `record_outcomes` (tables owned by db/migrations/0001).

The Core tables below only describe columns for typed reads and writes; they are never
created from Python (no `create_all`), the numbered migrations are the schema.
"""

import json

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    bindparam,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from src.schemas.operations import JobRun, JobRunFinish, JobRunStart, RecordOutcome

_metadata = MetaData()

job_runs = Table(
    "job_runs",
    _metadata,
    Column("run_id", String, primary_key=True),
    Column("process_id", String),
    Column("trigger_kind", String),
    Column("mode", String),
    Column("profile", String),
    Column("status", String),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("count_ok", Integer),
    Column("count_skipped", Integer),
    Column("count_failed", Integer),
    Column("error_code", String),
    Column("error_summary", String),
    Column("trace_id", String),
)

record_outcomes = Table(
    "record_outcomes",
    _metadata,
    Column("outcome_id", String, primary_key=True),
    Column("run_id", String),
    Column("object_type", String),
    Column("object_id", String),
    Column("result", String),
    Column("error_code", String),
    Column("error_type", String),
    Column("params", String),
    Column("technical_detail", String),
    Column("http_status", Integer),
    Column("trace_id", String),
    Column("recorded_at", DateTime(timezone=True)),
)

_OUTCOME_COLUMNS = [c.name for c in record_outcomes.columns]
# One outcome per object per run; portable upsert-ignore (SQLite >= 3.24, PostgreSQL >= 9.5).
_INSERT_OUTCOME = text(
    f"INSERT INTO record_outcomes ({', '.join(_OUTCOME_COLUMNS)})"
    f" VALUES ({', '.join(':' + c for c in _OUTCOME_COLUMNS)})"
    " ON CONFLICT (run_id, object_type, object_id) DO NOTHING"
).bindparams(bindparam("recorded_at", type_=DateTime(timezone=True)))


class JobRunRepository:
    """Each call is one short transaction on the shared engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def start_run(self, run: JobRunStart) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(insert(job_runs).values(status="running", **run.model_dump()))

    async def finish_run(self, finish: JobRunFinish) -> None:
        values = finish.model_dump(exclude={"run_id"})
        statement = update(job_runs).where(job_runs.c.run_id == finish.run_id).values(**values)
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def add_outcome(self, outcome: RecordOutcome) -> None:
        row = outcome.model_dump()
        row["params"] = json.dumps(outcome.params, ensure_ascii=False) if outcome.params else None
        async with self._engine.begin() as connection:
            await connection.execute(_INSERT_OUTCOME, row)

    async def list_runs(self, process_id: str, limit: int = 50) -> list[JobRun]:
        """Newest first."""
        statement = (
            select(job_runs)
            .where(job_runs.c.process_id == process_id)
            .order_by(job_runs.c.started_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [JobRun.model_validate(dict(row)) for row in rows]

    async def list_outcomes(self, run_id: str) -> list[RecordOutcome]:
        statement = (
            select(record_outcomes)
            .where(record_outcomes.c.run_id == run_id)
            .order_by(record_outcomes.c.recorded_at, record_outcomes.c.object_id)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [
            RecordOutcome.model_validate({**row, "params": json.loads(row["params"] or "{}")})
            for row in rows
        ]
