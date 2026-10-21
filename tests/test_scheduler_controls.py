"""SyncRunner dashboard controls: runtime flag toggles and run abort."""

import asyncio

import pytest

from src.pipelines.scheduler import SyncRunner
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

    async def run(self) -> SyncRunResult | None:
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


def _setup(auto_sync: bool = False) -> tuple[SyncRunner, BlockingPipeline, FakeSyncRepository]:
    repo = FakeSyncRepository(cursor=NOW)
    pipeline = BlockingPipeline(repo)
    config = FakeConfigStore(auto_sync_enabled=auto_sync)
    return SyncRunner(pipeline, repo, config, clock=lambda: NOW), pipeline, repo


@pytest.mark.asyncio
async def test_toggles_flip_flags_when_idle() -> None:
    runner, _, _ = _setup()

    live = await runner.toggle("dry_run")
    auto = await runner.toggle("auto_sync_enabled")

    assert live is not None and live.dry_run is False
    assert auto is not None and auto.auto_sync_enabled is True
    assert (await runner.config()).model_dump() == {"dry_run": False, "auto_sync_enabled": True}


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
    runner, pipeline, repo = _setup(auto_sync=True)
    ticks: list[float] = []
    resumed = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        resumed.set()
        await asyncio.Event().wait()

    daemon = asyncio.create_task(runner.run_forever(300, sleep=fake_sleep))
    await pipeline.started.wait()

    assert await runner.abort() is True
    await resumed.wait()

    assert ticks == [300] and not daemon.done() and repo.status == "IDLE"
    daemon.cancel()
    with pytest.raises(asyncio.CancelledError):
        await daemon
