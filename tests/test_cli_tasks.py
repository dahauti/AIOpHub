"""`python -m src.cli tasks`: argument parsing, summaries, lock bailout and settings."""

from pathlib import Path

import pytest

from src import cli
from src.config import Settings, get_settings
from src.services.ports import SyncRunResult
from src.services.task_runner import MODE_DIRECTIONS, DirectionRun, TaskMode, TaskRunReport

RESULT = SyncRunResult(synced=3, skipped=2, failed=1, validation_errors=1)


def _report(mode: TaskMode, dry_run: bool) -> TaskRunReport:
    runs = [
        DirectionRun(direction=direction, result=RESULT, elapsed_seconds=1.25)
        for direction in MODE_DIRECTIONS[mode]
    ]
    return TaskRunReport(dry_run=dry_run, runs=runs)


@pytest.fixture
def modes(monkeypatch: pytest.MonkeyPatch, isolated_env: None) -> list[TaskMode]:
    """Record modes passed by the CLI instead of touching BC, Frontu or SQLite."""

    async def fake_run(settings: Settings, mode: TaskMode) -> TaskRunReport:
        recorded.append(mode)
        return _report(mode, settings.sync_dry_run)

    recorded: list[TaskMode] = []
    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))
    monkeypatch.setattr(cli, "run_tasks", fake_run)
    return recorded


@pytest.mark.parametrize(
    "argv", [["tasks"], ["tasks", "--sync", "--inbound"], ["tasks", "--batch"]]
)
def test_invalid_task_arguments_exit_with_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2


@pytest.mark.parametrize("mode", ["sync", "outbound", "inbound"])
def test_parser_accepts_each_task_mode(mode: str) -> None:
    args = cli.build_parser().parse_args(["tasks", f"--{mode}"])

    assert (args.command, args.mode) == ("tasks", mode)


def test_sync_batch_command_is_still_available() -> None:
    args = cli.build_parser().parse_args(["sync", "--batch"])

    assert (args.command, args.batch) == ("sync", True)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("sync", ["outbound", "inbound"]), ("outbound", ["outbound"]), ("inbound", ["inbound"])],
)
def test_mode_prints_one_summary_line_per_direction(
    modes: list[TaskMode], capsys: pytest.CaptureFixture[str], mode: TaskMode, expected: list[str]
) -> None:
    assert cli.main(["tasks", f"--{mode}"]) == 0

    assert modes == [mode]
    banner, *lines = capsys.readouterr().out.splitlines()
    assert banner == (
        "[AIOpHUB] Env: DEV | Company: your-company-name "
        "| Frontu: https://your-subdomain.frontu.com/client/api/v2"
    )
    assert [line.split(":")[0] for line in lines] == [f"tasks --{d} [DRY RUN]" for d in expected]
    for fragment in ("processed=7", "synced=3", "skipped=2", "errors=2", "elapsed=1.2s"):
        assert fragment in lines[0]


def test_live_mode_is_labelled(
    modes: list[TaskMode], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SYNC_DRY_RUN", "false")
    get_settings.cache_clear()

    assert cli.main(["tasks", "--outbound"]) == 0

    assert capsys.readouterr().out.splitlines()[1].startswith("tasks --outbound [LIVE]: ")


def test_locked_run_exits_zero_without_summary(
    monkeypatch: pytest.MonkeyPatch, isolated_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    async def locked(_settings: Settings, _mode: TaskMode) -> TaskRunReport | None:
        return None

    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))
    monkeypatch.setattr(cli, "run_tasks", locked)

    assert cli.main(["tasks", "--sync"]) == 0
    [banner] = capsys.readouterr().out.splitlines()
    assert banner.startswith("[AIOpHUB] Env: DEV")


def test_unhandled_task_exception_exits_one(
    monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    async def boom(_settings: Settings, _mode: TaskMode) -> TaskRunReport | None:
        raise RuntimeError("Frontu unreachable")

    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))
    monkeypatch.setattr(cli, "run_tasks", boom)

    assert cli.main(["tasks", "--sync"]) == 1


def test_task_safety_buffer_comes_from_env_example(isolated_env: None) -> None:
    assert get_settings().task_safety_buffer_seconds == 900
