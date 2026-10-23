"""Operations Center read models (/centras): overview, process runs, run detail, record.

Read-only: built from job_runs / record_outcomes via `OperationsReadRepository`. Text is not
produced here; templates render codes through the catalogs (ADR 0002, ADR 0003).
"""

from datetime import datetime

from pydantic import BaseModel

from src.repositories.job_run_repository import JobRunRepository
from src.repositories.operations_read_repository import (
    OperationsReadRepository,
    ProcessRow,
    RunOutcome,
)
from src.schemas.operations import JobRun, RecordOutcome

RUNS_PER_PROCESS = 50
# Window scanned for "Reikia veiksmo": the newest outcomes across all processes.
ATTENTION_WINDOW = 500


class ProcessSummary(BaseModel):
    process: ProcessRow
    last_run: JobRun | None


class Overview(BaseModel):
    processes: list[ProcessSummary]
    attention: list[RunOutcome]


class ProcessRuns(BaseModel):
    process: ProcessRow
    runs: list[JobRun]


class RunDetail(BaseModel):
    process: ProcessRow | None
    run: JobRun
    outcomes: list[RecordOutcome]


class RecordDetail(BaseModel):
    outcome: RecordOutcome
    run: JobRun
    history: list[RunOutcome]


def duration_seconds(run: JobRun) -> float | None:
    return (run.finished_at - run.started_at).total_seconds() if run.finished_at else None


def needs_attention(outcomes: list[RunOutcome]) -> list[RunOutcome]:
    """Objects whose newest outcome is an error (a later success clears it); newest first."""
    seen: set[tuple[str, str]] = set()
    attention: list[RunOutcome] = []
    for item in outcomes:  # newest first
        key = (item.outcome.object_type, item.outcome.object_id)
        if key in seen:
            continue
        seen.add(key)
        if item.outcome.result == "error":
            attention.append(item)
    return attention


class OperationsView:
    def __init__(self, reads: OperationsReadRepository, runs: JobRunRepository) -> None:
        self._reads = reads
        self._runs = runs

    async def overview(self) -> Overview:
        summaries = []
        for process in await self._reads.list_processes():
            last = await self._runs.list_runs(process.id, limit=1)
            summaries.append(ProcessSummary(process=process, last_run=last[0] if last else None))
        latest = await self._reads.latest_outcomes(ATTENTION_WINDOW)
        return Overview(processes=summaries, attention=needs_attention(latest))

    async def process_runs(self, process_id: str) -> ProcessRuns | None:
        process = await self._process(process_id)
        if process is None:
            return None
        runs = await self._runs.list_runs(process_id, limit=RUNS_PER_PROCESS)
        return ProcessRuns(process=process, runs=runs)

    async def run_detail(self, run_id: str) -> RunDetail | None:
        run = await self._reads.get_run(run_id)
        if run is None:
            return None
        outcomes = await self._runs.list_outcomes(run_id)
        return RunDetail(process=await self._process(run.process_id), run=run, outcomes=outcomes)

    async def record(self, outcome_id: str) -> RecordDetail | None:
        outcome = await self._reads.get_outcome(outcome_id)
        if outcome is None:
            return None
        history = await self._reads.object_history(outcome.object_type, outcome.object_id)
        run = next((h.run for h in history if h.outcome.outcome_id == outcome_id), None)
        if run is None:
            return None
        return RecordDetail(outcome=outcome, run=run, history=history)

    async def _process(self, process_id: str) -> ProcessRow | None:
        return next((p for p in await self._reads.list_processes() if p.id == process_id), None)


def local(value: datetime) -> datetime:
    """Stored timestamps are UTC; screens show the server's local time (UX_GUIDELINES 7 sk.)."""
    return value.astimezone()
