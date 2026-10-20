"""App lifespan smoke test on temp SQLite with the daemon disabled (no BC/Frontu traffic)."""

import logging
from pathlib import Path

import pytest

from src import logging_setup
from src.main import create_app
from src.pipelines.scheduler import SyncRunner


@pytest.mark.asyncio
@pytest.mark.usefixtures("temp_database")
async def test_lifespan_wires_runner_logs_to_file_and_respects_safety_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(logging_setup, "DEFAULT_LOG_DIR", log_dir)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    app = create_app()

    try:
        async with app.router.lifespan_context(app):
            runner = app.state.sync_control
            assert isinstance(runner, SyncRunner)
            assert runner.dry_run is True
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)

    log_text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "[WARNING] [src.main]: SYNC_DRY_RUN is on: Frontu will not be modified" in log_text
    assert (
        "[WARNING] [src.pipelines.scheduler]: Background sync daemon is disabled by config"
        in log_text
    )
