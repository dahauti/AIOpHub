"""SyncRunner: scheduled daemon loop gated by auto-sync, on-demand trigger, snapshot window."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from src.pipelines.scheduler import SyncControl
from src.schemas.schedule import ScheduleSettings
from src.schemas.sync import DashboardSnapshot
from tests.pipeline_fakes import NOW, FakeSyncRepository
from tests.repository_fakes import FakeConfigStore
from tests.scheduler_fakes import Clock, FakePipeline, StopLoop
from tests.scheduler_fakes import make_runner as _runner
from tests.scheduler_fakes import schedule_store as _store


def test_runner_satisfies_dashboard_port() -> None:
    assert isinstance(_runner(FakePipeline()), SyncControl)


@pytest.mark.asyncio
async def test_daemon_runs_when_due_survives_errors_and_sleeps_tick(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = FakePipeline(outcomes=[RuntimeError("boom"), None])
    clock = Clock(stop_after=3)

    with caplog.at_level(logging.ERROR), pytest.raises(StopLoop):
        await _runner(pipeline, clock=clock).run_forever(300, sleep=clock.advance)

    assert pipeline.runs == 2  # nothing due at start; due at +5 min and +10 min
    assert clock.sleeps == [300, 300, 300]
    assert [r.getMessage() for r in caplog.records] == ["Scheduled sync run failed"]


@pytest.mark.asyncio
async def test_weekly_full_sync_supersedes_the_incremental_slot_it_shares() -> None:
    schedule = ScheduleSettings(
        nightly_time="12:05", full_sync_enabled=True, full_sync_weekday=1, full_sync_time="12:05"
    )  # NOW is Tuesday 12:00 UTC
    pipeline, clock = FakePipeline(), Clock(stop_after=4)
    times = iter([NOW.replace(minute=5), NOW.replace(minute=10), NOW.replace(day=21, minute=5)])

    async def jump(seconds: float) -> None:
        await clock.advance(seconds)
        clock.now = next(times)

    with pytest.raises(StopLoop):
        await _runner(pipeline, config=_store(schedule=schedule), clock=clock).run_forever(
            60, sleep=jump
        )

    assert pipeline.fulls == [True, False]


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
async def test_auto_sync_off_skips_runs_and_never_fires_missed_slots_when_re_enabled() -> None:
    pipeline, config, clock = FakePipeline(), _store(), Clock(stop_after=5)

    async def sleep(seconds: float) -> None:
        if len(clock.sleeps) in (0, 2):  # off over the +5 and +10 slots, back on at +15
            await config.toggle("auto_sync_enabled")
        await clock.advance(seconds)

    with pytest.raises(StopLoop):
        await _runner(pipeline, config=config, clock=clock).run_forever(300, sleep=sleep)

    assert pipeline.runs == 1  # re-planned from +15 when re-enabled, so the only run is at +20
    assert clock.sleeps == [300] * 5


@pytest.mark.asyncio
async def test_daemon_starts_background_loop_without_an_immediate_run() -> None:
    pipeline = FakePipeline()

    task = _runner(pipeline).start_daemon(tick_seconds=300)
    for _ in range(3):
        await asyncio.sleep(0)

    assert pipeline.runs == 0 and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_runner_exposes_runtime_config() -> None:
    config = FakeConfigStore(dry_run=True, auto_sync_enabled=False)

    current = await _runner(FakePipeline(), config=config).config()

    assert (current.dry_run, current.auto_sync_enabled) == (True, False)


@pytest.mark.asyncio
async def test_next_sync_follows_the_interval_plan_and_is_none_while_paused() -> None:
    clock = Clock(stop_after=2)
    config = _store()
    runner = _runner(FakePipeline(), config=config, clock=clock)

    assert await runner.next_sync() == NOW + timedelta(minutes=5)
    with pytest.raises(StopLoop):
        await runner.run_forever(300, sleep=clock.advance)
    assert await runner.next_sync() == NOW + timedelta(minutes=10)
    await config.toggle("auto_sync_enabled")
    assert await runner.next_sync() is None
