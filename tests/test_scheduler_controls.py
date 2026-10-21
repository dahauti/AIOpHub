"""SyncRunner dashboard controls: runtime flag toggles and run abort."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.pipelines.scheduler import SyncRunner
from src.schemas.schedule import ScheduleSettings
from src.schemas.sync import RUNNING, SyncRunResult
from tests.pipeline_fakes import NOW, FakeSyncRepository
from tests.repository_fakes import FakeConfigStore


class BlockingPipeline:
    """Holds RUNNING in the repository until released or cancelled, like the real pipeline."""

    def __init__(self, repo: FakeSyncRepository) -> None:
        self.repo = repo
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.runs = 0
        self.cancelled = 0
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def run(self, *, full: bool = False) -> SyncRunResult | None:
        self.runs += 1
        self._running = True
        await self.repo.try_acquire(NOW, NOW)
        self.started.set()
        try:
            await self.release.wait()
            await self.repo.release("IDLE", NOW)
            return SyncRunResult(cursor=NOW)
        except asyncio.CancelledError:
            self.cancelled += 1
            await self.repo.release("FAILED", NOW)
            raise
        finally:
            self._running = False


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _setup(
    auto_sync: bool = False, clock: Clock | None = None
) -> tuple[SyncRunner, BlockingPipeline, FakeSyncRepository]:
    repo = FakeSyncRepository(cursor=NOW)
    pipeline = BlockingPipeline(repo)
    config = FakeConfigStore(auto_sync_enabled=auto_sync)
    config.config = config.config.model_copy(
        update={"schedule_mode": "interval", "interval_minutes": 5}
    )
    runner = SyncRunner(pipeline, repo, config, clock=clock or Clock(), tz=UTC)
    return runner, pipeline, repo


@pytest.mark.asyncio
async def test_toggles_flip_flags_when_idle() -> None:
    runner, _, _ = _setup()

    live = await runner.toggle("dry_run")
    auto = await runner.toggle("auto_sync_enabled")

    assert live is not None and live.dry_run is False
    assert auto is not None and auto.auto_sync_enabled is True
    current = await runner.config()
    assert (current.dry_run, current.auto_sync_enabled) == (False, True)


@pytest.mark.asyncio
async def test_update_schedule_stores_it_without_touching_run_flags() -> None:
    runner, _, _ = _setup()
    weekly = ScheduleSettings(full_sync_enabled=True, full_sync_weekday=0, full_sync_time="03:30")

    config = await runner.update_schedule(weekly)

    assert config.key == weekly.key and (await runner.config()).key == weekly.key
    assert (config.dry_run, config.auto_sync_enabled) == (True, False)


@pytest.mark.asyncio
async def test_mode_is_locked_but_auto_sync_toggles_while_running() -> None:
    runner, pipeline, _ = _setup()
    assert runner.trigger()
    await pipeline.started.wait()

    assert await runner.toggle("dry_run") is None
    auto = await runner.toggle("auto_sync_enabled")

    assert auto is not None and auto.auto_sync_enabled is True
    assert (await runner.config()).dry_run is True
    await runner.shutdown()


@pytest.mark.asyncio
async def test_abort_cancels_manual_run_releases_idle_and_audits() -> None:
    runner, pipeline, repo = _setup()
    assert runner.trigger()
    await pipeline.started.wait()

    assert await runner.abort() is True

    assert pipeline.cancelled == 1
    assert repo.status == "IDLE" and repo.statuses[-2:] == ["FAILED", "IDLE"]
    assert [(e.entity_key, e.action) for e in repo.audit] == [("workspaces_vehicles", "ABORTED")]
    assert runner.trigger() is True
    await runner.shutdown()


@pytest.mark.asyncio
async def test_abort_when_idle_is_a_no_op() -> None:
    runner, _, repo = _setup()

    assert await runner.abort() is False
    assert repo.audit == [] and repo.statuses == []


@pytest.mark.asyncio
async def test_abort_clears_a_running_claim_without_an_in_process_run() -> None:
    runner, _, repo = _setup()
    repo.status = RUNNING

    assert await runner.abort() is True

    assert repo.status == "IDLE" and repo.audit[0].action == "ABORTED"


@pytest.mark.asyncio
async def test_abort_of_scheduled_run_keeps_the_daemon_loop_alive() -> None:
    clock = Clock()
    runner, pipeline, repo = _setup(auto_sync=True, clock=clock)
    ticks: list[float] = []
    resumed = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) == 1:
            clock.now += timedelta(minutes=5)  # the next tick finds the run due
            return
        resumed.set()
        await asyncio.Event().wait()

    daemon = asyncio.create_task(runner.run_forever(300, sleep=fake_sleep))
    await pipeline.started.wait()

    assert await runner.abort() is True
    await resumed.wait()

    assert ticks == [300, 300] and not daemon.done() and repo.status == "IDLE"
    daemon.cancel()
    with pytest.raises(asyncio.CancelledError):
        await daemon
