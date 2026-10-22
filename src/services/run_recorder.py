"""Job run and record outcome bookkeeping for the Operations Center (spec phase 1).

`RunRecorder.run(...)` wraps one sync run: it writes `job_runs` (running -> succeeded /
failed / aborted / skipped), sets a per-run trace ID that every log line carries, and lets
the services call `record_outcome(...)` for the run in progress.

Bookkeeping never changes a sync: persistence failures are logged as warnings and
swallowed, and exceptions from the sync itself are recorded and re-raised unchanged.
Stored technical text is sanitised (`src/services/redaction.py`).
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from src.config import Settings
from src.logging_setup import TRACE_ID, note_trace
from src.repositories.job_run_repository import JobRunRepository
from src.schemas.operations import (
    JobRunFinish,
    JobRunStart,
    OutcomeResult,
    ProcessId,
    RecordOutcome,
    RunMode,
    RunStatus,
    TriggerKind,
)
from src.services.error_catalog import classify, error_type
from src.services.ports import utc_now
from src.services.redaction import sanitize

logger = logging.getLogger(__name__)

TRACE_ID_CHARS = 16
# What bookkeeping may fail with without affecting the sync (DB errors, bad data, disk).
PERSISTENCE_ERRORS = (SQLAlchemyError, ValueError, OSError)


def run_profile(settings: Settings) -> str:
    """Environment profile of a run, e.g. `onprem_dev` (CLAUDE.md §5)."""
    return f"{settings.bc_active_profile}_{settings.app_env}"


@dataclass
class ActiveRun:
    """The run in progress; the caller fills in counts, the final mode and `skipped`."""

    run_id: str
    trace_id: str
    mode: RunMode
    count_ok: int = 0
    count_skipped: int = 0
    count_failed: int = 0
    skipped: bool = False


_CURRENT: ContextVar[ActiveRun | None] = ContextVar("current_run", default=None)


class RunRecorder:
    def __init__(self, repository: JobRunRepository, profile: str) -> None:
        self._repo = repository
        self._profile = profile

    @contextlib.asynccontextmanager
    async def run(
        self, process_id: ProcessId, trigger: TriggerKind, mode: RunMode
    ) -> AsyncIterator[ActiveRun]:
        run = ActiveRun(uuid.uuid4().hex, uuid.uuid4().hex[:TRACE_ID_CHARS], mode)
        trace_token, run_token = TRACE_ID.set(run.trace_id), _CURRENT.set(run)
        try:

            async def start() -> None:
                await self._repo.start_run(
                    JobRunStart(
                        run_id=run.run_id,
                        process_id=process_id,
                        trigger_kind=trigger,
                        mode=mode,
                        profile=self._profile,
                        started_at=utc_now(),
                        trace_id=run.trace_id,
                    )
                )

            await self._safely("start", start)
            logger.info("Job run started", extra={"run_id": run.run_id, "sync_module": process_id})
            try:
                yield run
            except asyncio.CancelledError:
                await self._finish(run, "aborted")
                raise
            except Exception as error:  # recorded, then re-raised with its trace noted
                await self._finish(run, "failed", error)
                note_trace(error, run.trace_id)
                raise
            await self._finish(run, "skipped" if run.skipped else "succeeded")
        finally:
            _CURRENT.reset(run_token)
            TRACE_ID.reset(trace_token)

    async def record_outcome(
        self,
        object_type: str,
        object_id: str,
        result: OutcomeResult,
        *,
        error_code: str | None = None,
        params: dict[str, str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Outcome of one object in the current run; ignored outside a run."""
        run = _CURRENT.get()
        if run is None:
            return

        async def write() -> None:
            classified = classify(error) if error is not None else None
            code = error_code or (classified.code if classified else None)
            outcome = RecordOutcome(
                outcome_id=uuid.uuid4().hex,
                run_id=run.run_id,
                object_type=object_type,
                object_id=object_id,
                result=result,
                error_code=code,
                error_type=error_type(code) if code and result == "error" else None,
                params=params or {},
                technical_detail=sanitize(classified.detail) if classified else None,
                http_status=classified.http_status if classified else None,
                trace_id=run.trace_id,
                recorded_at=utc_now(),
            )
            await self._repo.add_outcome(outcome)

        await self._safely("outcome", write)

    async def record_failure(self, object_type: str, object_id: str, error: BaseException) -> None:
        """The one object a failing run was working on (a canary): error outcome, 1 failed."""
        run = _CURRENT.get()
        if run is not None:
            run.count_failed = 1
        await self.record_outcome(object_type, object_id, "error", error=error)

    async def _finish(
        self, run: ActiveRun, status: RunStatus, error: BaseException | None = None
    ) -> None:

        async def write() -> None:
            classified = classify(error) if error is not None else None
            finish = JobRunFinish(
                run_id=run.run_id,
                status=status,
                mode=run.mode,
                finished_at=utc_now(),
                count_ok=run.count_ok,
                count_skipped=run.count_skipped,
                count_failed=run.count_failed,
                error_code=classified.code if classified else None,
                error_summary=sanitize(classified.detail) if classified else None,
            )
            await self._repo.finish_run(finish)

        await self._safely("finish", write)
        logger.info("Job run %s", status, extra={"run_id": run.run_id})

    async def _safely(self, step: str, write: Callable[[], Awaitable[None]]) -> None:
        """Build and store one bookkeeping row; a failure is a warning, never an exception."""
        try:
            await write()
        except PERSISTENCE_ERRORS as error:
            logger.warning(
                "Run bookkeeping (%s) failed; the sync continues: %s",
                step,
                sanitize(f"{type(error).__name__}: {error}"),
            )
