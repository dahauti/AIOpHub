"""SyncRunner: periodic daemon loop gated by auto-sync, on-demand trigger, snapshot window."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from src.pipelines.scheduler import SyncControl, SyncRunner
from src.schemas.sync import DashboardSnapshot, SyncRunResult
from tests.pipeline_fakes import NOW, FakeSyncRepository
from tests.repository_fakes import FakeConfigStore


class FakePipeline:
    def __init__(self, outcomes: list[Exception | None] | None = None, block: bool = False) -> None:
        self.outcomes = outcomes or []
        self.block = block
        self.release = asyncio.Event()
        self.runs = 0
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def run(self) -> SyncRunResult | None:
        self.runs += 1
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


def _runner(
    pipeline: FakePipeline,
    repo: FakeSyncRepository | None = None,
    config: FakeConfigStore | None = None,
) -> SyncRunner:
    repository = repo or FakeSyncRepository(cursor=NOW)
    store = config or FakeConfigStore(auto_sync_enabled=True)
    return SyncRunner(pipeline, repository, store, clock=lambda: NOW)


def test_runner_satisfies_dashboard_port() -> None:
    assert isinstance(_runner(FakePipeline()), SyncControl)


@pytest.mark.asyncio
async def test_daemon_runs_immediately_survives_errors_and_sleeps_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = FakePipeline(outcomes=[RuntimeError("boom"), None])
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopLoop

    with caplog.at_level(logging.ERROR), pytest.raises(StopLoop):
        await _runner(pipeline).run_forever(300, sleep=fake_sleep)

    assert pipeline.runs == 2
    assert sleeps == [300, 300]
    assert [r.getMessage() for r in caplog.records] == ["Scheduled sync run failed"]


@pytest.mark.asyncio
async def test_trigger_starts_background_run_and_rejects_while_active() -> None:
    pipeline = FakePipeline(block=True)
    runner = _runner(pipeline)

    assert runner.trigger() is True
    assert runner.trigger() is False
    await asyncio.sleep(0)
    assert pipeline.is_running
    assert runner.trigger() is False

    pipeline.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert pipeline.runs == 1
    assert runner.trigger() is True
    await runner.shutdown()


@pytest.mark.asyncio
async def test_failed_manual_run_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    runner = _runner(FakePipeline(outcomes=[RuntimeError("down")]))

    with caplog.at_level(logging.ERROR):
        assert runner.trigger() is True
        for _ in range(3):
            await asyncio.sleep(0)

    assert [r.getMessage() for r in caplog.records] == ["Manual sync run failed"]


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_manual_run() -> None:
    pipeline = FakePipeline(block=True)
    runner = _runner(pipeline)
    runner.trigger()
    await asyncio.sleep(0)

    await runner.shutdown()

    assert runner.trigger() is True
    await runner.shutdown()


@pytest.mark.asyncio
async def test_snapshot_counts_from_utc_midnight() -> None:
    captured: list[datetime] = []

    class RecordingRepo(FakeSyncRepository):
        async def dashboard_snapshot(self, since: datetime, limit: int) -> DashboardSnapshot:
            captured.append(since)
            return await super().dashboard_snapshot(since, limit)

    await _runner(FakePipeline(), RecordingRepo(cursor=NOW)).snapshot()

    assert captured == [datetime(2026, 10, 20, tzinfo=UTC)]


@pytest.mark.asyncio
async def test_auto_sync_off_skips_scheduled_runs_until_toggled_on() -> None:
    pipeline = FakePipeline()
    config = FakeConfigStore(auto_sync_enabled=False)
    ticks: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) == 1:
            await config.toggle("auto_sync_enabled")
        if len(ticks) == 2:
            raise StopLoop

    with pytest.raises(StopLoop):
        await _runner(pipeline, config=config).run_forever(300, sleep=fake_sleep)

    assert (pipeline.runs, ticks) == (1, [300, 300])


@pytest.mark.asyncio
async def test_daemon_starts_background_loop() -> None:
    pipeline = FakePipeline()

    task = _runner(pipeline).start_daemon(interval_seconds=300)
    for _ in range(3):
        await asyncio.sleep(0)

    assert pipeline.runs == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_runner_exposes_runtime_config() -> None:
    config = FakeConfigStore(dry_run=True, auto_sync_enabled=False)

    current = await _runner(FakePipeline(), config=config).config()

    assert (current.dry_run, current.auto_sync_enabled) == (True, False)
