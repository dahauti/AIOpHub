"""Every log line of a job run carries its trace ID; lines outside a run carry `-`."""

import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from src.logging_setup import configure_logging
from src.repositories.job_run_repository import JobRunRepository
from src.repositories.migration_runner import apply_migrations
from src.services.run_recorder import RunRecorder


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
