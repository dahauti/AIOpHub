"""Centralised logging: console + 5 MB x3 rotating file, idempotent setup."""

import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from src.logging_setup import LOG_FORMAT, configure_logging


@pytest.fixture
def restore_root_logging() -> Iterator[logging.Logger]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield root
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def _ours(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if (h.name or "").startswith("aiophub-")]


def test_creates_log_dir_and_attaches_console_and_rotating_file(
    tmp_path: Path, restore_root_logging: logging.Logger
) -> None:
    log_dir = tmp_path / "logs"

    log_file = configure_logging("WARNING", log_dir)

    assert log_file == log_dir / "app.log"
    assert log_dir.is_dir()
    assert restore_root_logging.level == logging.WARNING
    handlers = _ours(restore_root_logging)
    file_handlers = [h for h in handlers if isinstance(h, RotatingFileHandler)]
    consoles = [h for h in handlers if not isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1 and len(consoles) == 1
    assert isinstance(consoles[0], logging.StreamHandler)
    assert file_handlers[0].maxBytes == 5 * 1024 * 1024
    assert file_handlers[0].backupCount == 3
    assert all(h.formatter is not None and h.formatter._fmt == LOG_FORMAT for h in handlers)


def test_records_are_written_in_the_required_format(
    tmp_path: Path, restore_root_logging: logging.Logger
) -> None:
    log_file = configure_logging("INFO", tmp_path / "logs")

    logging.getLogger("src.pipelines.test").info("hello %s", "world")
    logging.getLogger("src.pipelines.test").debug("filtered out")
    for handler in _ours(restore_root_logging):
        handler.flush()

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].endswith(" [INFO] [src.pipelines.test]: hello world")


def test_repeated_setup_replaces_instead_of_duplicating_handlers(
    tmp_path: Path, restore_root_logging: logging.Logger
) -> None:
    configure_logging("INFO", tmp_path / "logs")
    configure_logging("DEBUG", tmp_path / "logs")

    assert len(_ours(restore_root_logging)) == 2
    assert restore_root_logging.level == logging.DEBUG
