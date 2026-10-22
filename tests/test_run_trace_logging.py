"""Every log line of a job run carries its trace ID; lines outside a run carry `-`."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import Settings, get_settings
from src.domain.canary import CanaryTarget
from src.logging_setup import NO_TRACE, TRACE_ID, configure_logging, failed_run_trace
from src.repositories.job_run_repository import JobRunRepository
from src.repositories.migration_runner import apply_migrations
from src.services.run_recorder import RunRecorder
from src.services.task_control import TaskSyncControl
from src.services.task_runner import TaskMode, TaskRunReport

CANARY = CanaryTarget(kind="order", value="SO00001")


@pytest_asyncio.fixture
async def recorder(tmp_path: Path) -> AsyncIterator[RunRecorder]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'ops.db').as_posix()}")
    await apply_migrations(engine)
    yield RunRecorder(JobRunRepository(engine), "onprem_dev")
    await engine.dispose()


@pytest.fixture
def restore_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.asyncio
@pytest.mark.usefixtures("restore_root_logging")
async def test_log_lines_carry_the_run_trace_id(recorder: RunRecorder, tmp_path: Path) -> None:
    log_file = configure_logging("INFO", tmp_path / "logs")
    log = logging.getLogger("src.services.test")

    log.info("before")
    async with recorder.run("tasks_sync", "manual", "LIVE") as run:
        log.info("inside")
    log.info("after")
    for handler in logging.getLogger().handlers:
        handler.flush()

    lines = {line.rsplit(": ", 1)[-1]: line for line in log_file.read_text("utf-8").splitlines()}
    assert "[trace=-]" in lines["before"] and "[trace=-]" in lines["after"]
    assert f"[trace={run.trace_id}]" in lines["inside"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("restore_root_logging", "temp_database")
async def test_ui_run_failures_are_logged_in_the_failed_run_trace(
    recorder: RunRecorder, tmp_path: Path
) -> None:
    log_file = configure_logging("INFO", tmp_path / "logs")
    traces: list[str] = []

    async def failing_run() -> None:
        async with recorder.run("tasks_sync", "manual", "LIVE") as run:
            traces.append(run.trace_id)
            raise RuntimeError("boom")

    async def runner(_settings: Settings, _mode: TaskMode) -> TaskRunReport | None:
        await failing_run()
        return None

    async def single(
        _settings: Settings, _target: CanaryTarget, *, force_live: bool = False
    ) -> TaskRunReport | None:
        await failing_run()
        return None

    control = TaskSyncControl(get_settings(), runner=runner, single_runner=single)
    for start in (control.trigger, lambda: control.trigger_single(CANARY)):
        assert start() is True
        while control.busy:
            await asyncio.sleep(0.01)
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = log_file.read_text("utf-8")
    assert f"[trace={traces[0]}] [ERROR] [src.services.task_control]: UI-triggered" in text
    assert f"[trace={traces[1]}] [ERROR] [src.services.task_control]: Task canary" in text


def test_error_outside_a_run_keeps_the_empty_trace() -> None:
    with failed_run_trace(RuntimeError("before any run")):
        assert TRACE_ID.get() == NO_TRACE
