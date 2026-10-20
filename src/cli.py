"""Headless entry point for OS schedulers: `python -m src.cli sync --batch`.

Exit codes: 0 on a completed run or when another run already holds the module lock,
1 on an unhandled exception, 2 on invalid arguments (argparse).
"""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Sequence

from src.config import Settings, get_settings
from src.database import get_engine
from src.logging_setup import configure_logging
from src.pipelines.sync_vehicles import sync_vehicles_batch
from src.schemas.sync import SyncRunResult

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="run the BC -> Frontu vehicle sync")
    sync.add_argument(
        "--batch",
        action="store_true",
        required=True,
        help="process the VehicleCard delta since the stored cursor",
    )
    return parser


def format_summary(result: SyncRunResult, elapsed: float, settings: Settings) -> str:
    mode = "DRY RUN" if settings.sync_dry_run else "LIVE"
    return (
        f"sync --batch [{mode}]: processed={result.processed} planned={result.planned} "
        f"skipped={result.skipped} errors={result.failed} elapsed={elapsed:.1f}s"
    )


async def _run_batch() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    started = time.monotonic()
    try:
        result = await sync_vehicles_batch(settings)
    finally:
        await get_engine().dispose()
    if result is None:
        logger.warning("Batch sync skipped: another run holds the module lock in sync_states")
        return EXIT_OK
    print(format_summary(result, time.monotonic() - started, settings))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    try:
        return asyncio.run(_run_batch())
    except Exception:
        logger.exception("Batch sync failed")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
