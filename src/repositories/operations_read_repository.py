"""Read-only SQL for the Operations Center screens (/centras): processes, runs, outcomes.

Nothing here writes; the tables are owned by db/migrations/0001 (ADR 0003).
"""

from pydantic import BaseModel
from sqlalchemy import Column, ColumnElement, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.repositories.job_run_repository import job_runs, outcome_from_row, record_outcomes
from src.schemas.operations import JobRun, RecordOutcome

processes = Table(
    "processes",
    MetaData(),
    Column("id", String, primary_key=True),
    Column("name_key", String),
    Column("direction", String),
    Column("owner", String),
)


class ProcessRow(BaseModel):
    id: str
    name_key: str
    direction: str
    owner: str | None = None


class RunOutcome(BaseModel):
    """One outcome with the run it belongs to."""

    outcome: RecordOutcome
    run: JobRun


class OperationsReadRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_processes(self) -> list[ProcessRow]:
        async with self._engine.connect() as connection:
            rows = (await connection.execute(select(processes).order_by(processes.c.id))).all()
        return [ProcessRow.model_validate(dict(row._mapping)) for row in rows]

    async def get_run(self, run_id: str) -> JobRun | None:
        statement = select(job_runs).where(job_runs.c.run_id == run_id)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return JobRun.model_validate(dict(row)) if row is not None else None

    async def get_outcome(self, outcome_id: str) -> RecordOutcome | None:
        statement = select(record_outcomes).where(record_outcomes.c.outcome_id == outcome_id)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return outcome_from_row(row) if row is not None else None

    async def latest_outcomes(self, limit: int) -> list[RunOutcome]:
        """Newest outcomes of all processes first."""
        return await self._with_runs(limit=limit)

    async def object_history(self, object_type: str, object_id: str) -> list[RunOutcome]:
        """Every outcome of one object, newest first."""
        return await self._with_runs(
            record_outcomes.c.object_type == object_type,
            record_outcomes.c.object_id == object_id,
        )

    async def _with_runs(
        self, *where: ColumnElement[bool], limit: int | None = None
    ) -> list[RunOutcome]:
        """Matching outcomes newest first, each with its run (two reads, one connection)."""
        statement = (
            select(record_outcomes)
            .where(*where)
            .order_by(record_outcomes.c.recorded_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
            outcomes = [outcome_from_row(row) for row in rows]
            run_ids = sorted({o.run_id for o in outcomes})
            runs_statement = select(job_runs).where(job_runs.c.run_id.in_(run_ids))
            run_rows = (await connection.execute(runs_statement)).mappings().all()
        runs = {str(r["run_id"]): JobRun.model_validate(dict(r)) for r in run_rows}
        return [RunOutcome(outcome=o, run=runs[o.run_id]) for o in outcomes if o.run_id in runs]
