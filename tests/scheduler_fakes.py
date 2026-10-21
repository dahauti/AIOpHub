"""Scheduler test doubles: a scriptable pipeline, an advancing clock and runner factories."""

import asyncio
from datetime import UTC, datetime, timedelta

from src.pipelines.scheduler import SyncRunner
from src.schemas.schedule import ScheduleSettings
from src.schemas.sync import SyncRunResult
from tests.pipeline_fakes import NOW, FakeSyncRepository
from tests.repository_fakes import FakeConfigStore


class FakePipeline:
    def __init__(self, outcomes: list[Exception | None] | None = None, block: bool = False) -> None:
        self.outcomes = outcomes or []
        self.block = block
        self.release = asyncio.Event()
        self.runs = 0
        self.fulls: list[bool] = []
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def run(self, *, full: bool = False) -> SyncRunResult | None:
        self.runs += 1
        self.fulls.append(full)
        self._running = True
        try:
            if self.block:
                await self.release.wait()
            outcome = self.outcomes.pop(0) if self.outcomes else None
            if outcome is not None:
                raise outcome
            return SyncRunResult(cursor=NOW)
        finally:
            self._running = False


class StopLoop(BaseException):
    """Escapes the daemon loop from the fake sleep."""


class Clock:
    """Mutable test clock; `advance` doubles as the daemon's sleep."""

    def __init__(self, stop_after: int | None = None) -> None:
        self.now = NOW
        self.step = timedelta(minutes=5)
        self.stop_after = stop_after
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def advance(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if len(self.sleeps) == self.stop_after:
            raise StopLoop
        self.now += self.step


EVERY_5_MIN = ScheduleSettings(schedule_mode="interval", interval_minutes=5)


def schedule_store(
    auto_sync: bool = True, schedule: ScheduleSettings = EVERY_5_MIN
) -> FakeConfigStore:
    store = FakeConfigStore(auto_sync_enabled=auto_sync)
    store.config = store.config.model_copy(update=schedule.model_dump())
    return store


def make_runner(
    pipeline: FakePipeline,
    repo: FakeSyncRepository | None = None,
    config: FakeConfigStore | None = None,
    clock: Clock | None = None,
) -> SyncRunner:
    repository = repo or FakeSyncRepository(cursor=NOW)
    store = config or schedule_store()
    return SyncRunner(pipeline, repository, store, clock=clock or Clock(), tz=UTC)
