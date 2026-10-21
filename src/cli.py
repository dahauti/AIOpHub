"""Headless entry point for OS schedulers.

`python -m src.cli sync --batch` runs the Module 1 vehicle sync;
`python -m src.cli tasks --sync|--outbound|--inbound` runs the Module 2 task sync.

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
from src.database import get_engine, get_sessionmaker, init_db
from src.domain.canary import MAX_TARGET_CHARS, CanaryTarget, is_guid
from src.logging_setup import configure_logging
from src.pipelines.sync_vehicles import sync_vehicles_batch
from src.schemas.sync import SyncRunResult
from src.services.link_status import persist_link_health
from src.services.task_runner import (
    DirectionRun,
    TaskMode,
    run_bootstrap,
    run_single,
    run_tasks,
)

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
    tasks = commands.add_parser("tasks", help="run the BC <-> Frontu Service Order task sync")
    modes = tasks.add_mutually_exclusive_group(required=True)
    for mode, help_text in (
        ("sync", "outbound push, then inbound pull"),
        ("outbound", "BC -> Frontu push only"),
        ("inbound", "Frontu -> BC pull only"),
    ):
        modes.add_argument(
            f"--{mode}", dest="mode", action="store_const", const=mode, help=help_text
        )
    modes.add_argument(
        "--bootstrap-watermark",
        dest="mode",
        action="store_const",
        const="bootstrap",
        help="stamp the newest Frontu task version as baseline without syncing history",
    )
    modes.add_argument("--single-so", metavar="SO_NO", type=_order_no, help="canary: one BC order")
    modes.add_argument("--single-task", metavar="GUID", type=_task_guid, help="canary: one task")
    return parser


def _order_no(value: str) -> str:
    if not value.strip() or len(value.strip()) > MAX_TARGET_CHARS:
        raise argparse.ArgumentTypeError("expected a BC Service Order No")
    return value.strip()


def _task_guid(value: str) -> str:
    if not is_guid(value.strip()):
        raise argparse.ArgumentTypeError("expected a Frontu task GUID (8-4-4-4-12 hex)")
    return value.strip()


def canary_target(args: argparse.Namespace) -> CanaryTarget | None:
    if args.single_so:
        return CanaryTarget(kind="order", value=args.single_so)
    if args.single_task:
        return CanaryTarget(kind="task", value=args.single_task)
    return None


def format_banner(settings: Settings) -> str:
    return (
        f"[AIOpHUB] Env: {settings.app_env.upper()} | Company: {settings.active_bc_company} "
        f"| Frontu: {settings.active_frontu_base_url}"
    )


def format_task_summary(run: DirectionRun, dry_run: bool, label: str | None = None) -> str:
    mode = "DRY RUN" if dry_run else "LIVE"
    result = run.result
    return (
        f"tasks --{label or run.direction} [{mode}]: processed={result.processed} "
        f"synced={result.synced} skipped={result.skipped} errors={result.errors} "
        f"elapsed={run.elapsed_seconds:.1f}s"
    )


def format_summary(result: SyncRunResult, elapsed: float) -> str:
    mode = "DRY RUN" if result.dry_run else "LIVE"
    return (
        f"sync --batch [{mode}]: processed={result.processed} planned={result.planned} "
        f"skipped={result.skipped} errors={result.failed} elapsed={elapsed:.1f}s"
    )


async def _run_batch() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    print(format_banner(settings))
    started = time.monotonic()
    try:
        await init_db()  # a fresh install or deleted sync.db gets its tables without the web app
        result = await sync_vehicles_batch(settings)
    finally:
        await persist_link_health(get_sessionmaker())
        await get_engine().dispose()
    if result is None:
        logger.warning("Batch sync skipped: another run holds the module lock in sync_states")
        return EXIT_OK
    print(format_summary(result, time.monotonic() - started))
    return EXIT_OK


async def _run_tasks(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    print(format_banner(settings))
    target = canary_target(args)
    if args.mode == "bootstrap":
        baseline = await run_bootstrap(settings)
        if baseline is not None:
            print(f"tasks --bootstrap-watermark: baseline version={baseline}")
            return EXIT_OK
        report = None
    elif target is not None:
        report = await run_single(settings, target)
    else:
        mode: TaskMode = args.mode
        report = await run_tasks(settings, mode)
    if report is None:
        logger.warning("Task sync skipped: another run holds the tasks_sync lock")
        return EXIT_OK
    label = None
    if target is not None:
        flag = "single-so" if target.kind == "order" else "single-task"
        label = f"{flag} {target.value}"
    for run in report.runs:
        print(format_task_summary(run, report.dry_run, label))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tasks":
        try:
            return asyncio.run(_run_tasks(args))
        except Exception:
            logger.exception("Task sync failed")
            return EXIT_ERROR
    try:
        return asyncio.run(_run_batch())
    except Exception:
        logger.exception("Batch sync failed")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
