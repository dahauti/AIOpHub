"""Centralised logging: console stream plus a size-rotated file under <project root>/logs.

Every line carries `[trace=<id>]`: the trace ID of the job run in progress (see
`src/services/run_recorder.py`), or `-` outside a run. An exception that failed a run is
noted with that run's trace ID, so an error logged after the run joins the same trace.
"""

import contextlib
import logging
import sys
from collections.abc import Iterator
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE_NAME = "app.log"
LOG_FORMAT = "%(asctime)s [trace=%(trace_id)s] [%(levelname)s] [%(name)s]: %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
_HANDLER_PREFIX = "aiophub-"
NO_TRACE = "-"

TRACE_ID: ContextVar[str] = ContextVar("trace_id", default=NO_TRACE)
TRACE_NOTE = "trace_id="


def note_trace(error: BaseException, trace_id: str) -> None:
    """Tag `error` with the trace of the run it failed (shown in its traceback too)."""
    error.add_note(f"{TRACE_NOTE}{trace_id}")


@contextlib.contextmanager
def failed_run_trace(error: BaseException) -> Iterator[None]:
    """Log inside the trace of the run `error` failed; `-` if it failed outside a run."""
    notes: list[str] = getattr(error, "__notes__", [])
    traces = [note.removeprefix(TRACE_NOTE) for note in notes if note.startswith(TRACE_NOTE)]
    token = TRACE_ID.set(traces[0] if traces else NO_TRACE)
    try:
        yield
    finally:
        TRACE_ID.reset(token)


class TraceIdFilter(logging.Filter):
    """Stamp the current run's trace ID on every record passing our handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = TRACE_ID.get()
        return True


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
        handler.addFilter(TraceIdFilter())
        root.addHandler(handler)
    root.setLevel(level)
    return log_file
