"""Centralised logging: console stream plus a size-rotated file under <project root>/logs."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE_NAME = "app.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
_HANDLER_PREFIX = "aiophub-"


def configure_logging(level: str, log_dir: Path | None = None) -> Path:
    """Attach console + rotating file handlers to the root logger; safe to call repeatedly.

    Returns the active log file path. Only handlers installed by this function are
    replaced, so uvicorn's and pytest's own handlers are left untouched.
    """
    directory = log_dir if log_dir is not None else DEFAULT_LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / LOG_FILE_NAME

    root = logging.getLogger()
    for handler in [h for h in root.handlers if (h.name or "").startswith(_HANDLER_PREFIX)]:
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    console = logging.StreamHandler(sys.stderr)
    console.name = f"{_HANDLER_PREFIX}console"
    rotating = RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    rotating.name = f"{_HANDLER_PREFIX}file"
    for handler in (console, rotating):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level)
    return log_file
