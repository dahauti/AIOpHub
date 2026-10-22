"""Vehicle runs recorded in `job_runs` (spec phase 1); the wrapped pipeline is unchanged.

The run mode is read from the pipeline after the run (it resolves dry-run inside its lock),
so no extra config read happens before a sync. A run that finds the module claimed
elsewhere (`None`) is recorded as `skipped`. Per-vehicle `record_outcomes` are a follow-up.
"""

from typing import Final, Protocol

from src.config import Settings
from src.database import get_engine
from src.repositories.job_run_repository import JobRunRepository
from src.schemas.operations import TriggerKind, mode_of
from src.schemas.sync import SyncRunResult
from src.services.run_recorder import RunRecorder, run_profile

PROCESS: Final = "workspaces_vehicles"


class VehiclePipeline(Protocol):
    @property
    def is_running(self) -> bool: ...
    @property
    def dry_run(self) -> bool: ...
    async def run(self, *, full: bool = False) -> SyncRunResult | None: ...


class RecordedPipeline:
    """`RunnablePipeline` for the scheduler: same runs, plus one `job_runs` row each."""

    def __init__(self, pipeline: VehiclePipeline, recorder: RunRecorder) -> None:
        self._pipeline = pipeline
        self._recorder = recorder

    @property
    def is_running(self) -> bool:
        return self._pipeline.is_running

    async def run(
        self, *, full: bool = False, trigger: TriggerKind = "schedule"
    ) -> SyncRunResult | None:
        mode = mode_of(self._pipeline.dry_run)
        async with self._recorder.run(PROCESS, trigger, mode) as run:
            try:
                result = await self._pipeline.run(full=full)
            finally:
                run.mode = mode_of(self._pipeline.dry_run)
            if result is None:
                run.skipped = True
                return None
            run.count_ok = result.processed + result.planned
            run.count_skipped = result.skipped
            run.count_failed = result.failed
            return result


def recorded(pipeline: VehiclePipeline, settings: Settings) -> RecordedPipeline:
    """Wrap a pipeline with a recorder on the shared engine (web app and CLI batch)."""
    return RecordedPipeline(
        pipeline, RunRecorder(JobRunRepository(get_engine()), run_profile(settings))
    )
