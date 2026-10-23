"""Single-entity canary: target parsing, service paths, locked runner, CLI flags and UI form."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from src import cli
from src.config import Settings, get_settings
from src.database import get_engine
from src.domain.bc_models import BCServiceOrderHeader
from src.domain.canary import CanaryTarget, parse_canary_target
from src.domain.frontu_models import FrontuTask
from src.services.ports import SyncRunResult
from src.services.task_control import TaskSyncControl
from src.services.task_inbound import TaskInboundService
from src.services.task_outbound import TaskOutboundService
from src.services.task_runner import DirectionRun, TaskRunReport, run_single
from tests.task_fakes import NOW, FakeBC, FakeFrontu, Stores, stores
from tests.task_runner_fakes import MockBackends, audited_operations, backends

__all__ = ["backends", "stores"]

GUID = "3f9c2a1e-0b7d-4c55-9e1a-6d2f8b4a7c10"
ORDER = CanaryTarget(kind="order", value="SO00001")
REPORT = TaskRunReport(
    dry_run=True,
    runs=[DirectionRun(direction="outbound", result=SyncRunResult(synced=1), elapsed_seconds=0.1)],
)


@pytest.mark.parametrize(
    ("raw", "kind"), [(" SO00042 ", "order"), (GUID, "task"), (GUID.upper(), "task")]
)
def test_target_is_classified_by_guid_shape(raw: str, kind: str) -> None:
    target = parse_canary_target(raw)
    assert (target.kind, target.value) == (kind, raw.strip())


@pytest.mark.parametrize("raw", ["", "   ", "S" * 65])
def test_blank_or_oversized_target_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_canary_target(raw)


def _outbound(bc: FakeBC, frontu: FakeFrontu, stores: Stores) -> TaskOutboundService:
    initial = NOW - timedelta(days=1)
    return TaskOutboundService(
        bc, frontu, stores.watermarks, stores.states, initial_watermark=initial, now=lambda: NOW
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "synced", "skipped", "invalid"),
    [("FRONTU", 1, 0, 0), ("DONE", 0, 1, 0), (None, 0, 0, 1)],
)
async def test_single_order_pushes_only_released_orders(
    stores: Stores, status: str | None, synced: int, skipped: int, invalid: int
) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    if status is not None:
        bc.orders["SO00001"] = BCServiceOrderHeader(
            No="SO00001", SelltoCustomerNo="C00010", DocumentStatus=status
        )

    result = await _outbound(bc, frontu, stores).sync_single_order("SO00001")

    assert (result.synced, result.skipped, result.validation_errors) == (synced, skipped, invalid)
    assert len(frontu.upserts) == synced
    assert await stores.watermarks.get_raw_watermark("operative_tasks_bc") is None


@pytest.mark.asyncio
async def test_single_frontu_task_is_pulled_without_moving_the_watermark(stores: Stores) -> None:
    bc, frontu = FakeBC(), FakeFrontu()
    bc.orders["SO00001"] = BCServiceOrderHeader(No="SO00001", DocumentStatus="TASKERYJE")
    frontu.delta = [FrontuTask(guid=GUID, systemId="SO00001", status=400, version=9)]
    service = TaskInboundService(bc, frontu, stores.watermarks, stores.states, now=lambda: NOW)

    found = await service.sync_single_frontu_task(GUID)
    missing = await service.sync_single_frontu_task("nope")

    assert (found.synced, missing.validation_errors) == (1, 1)
    assert bc.header_patches[0][1]["DocumentStatus"] == "DONE"
    assert frontu.versions == []
    assert await stores.watermarks.get_version_watermark("operative_tasks_frontu") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "direction"),
    [(ORDER, "outbound"), (CanaryTarget(kind="task", value="t-1"), "inbound")],
)
async def test_run_single_is_locked_dry_run_and_audited(
    backends: MockBackends, target: CanaryTarget, direction: str
) -> None:
    report = await run_single(get_settings(), target)

    assert report is not None and report.dry_run is True
    assert [(r.direction, r.result.synced) for r in report.runs] == [(direction, 1)]
    assert backends.writes == []
    assert await audited_operations()


@pytest.mark.parametrize(
    ("flag", "value", "label"),
    [
        ("--single-so", "SO00001", "single-so SO00001"),
        ("--single-task", GUID, f"single-task {GUID}"),
    ],
)
def test_cli_single_flags_run_one_canary(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: None,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
    label: str,
) -> None:
    seen: list[CanaryTarget] = []

    async def fake_single(_settings: Settings, target: CanaryTarget) -> TaskRunReport:
        seen.append(target)
        return REPORT

    monkeypatch.setattr(cli, "configure_logging", lambda _level: Path("unused.log"))
    monkeypatch.setattr(cli, "run_single", fake_single)

    assert cli.main(["tasks", flag, value]) == 0

    assert [t.value for t in seen] == [value]
    assert capsys.readouterr().out.splitlines()[1].startswith(f"tasks --{label} [DRY RUN]: ")


@pytest.mark.parametrize(
    "argv",
    [
        ["tasks", "--single-task", "SO00001"],
        ["tasks", "--single-so", " "],
        ["tasks", "--sync", "--single-so", "SO1"],
    ],
)
def test_cli_rejects_bad_canary_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "state", "text"),
    [(REPORT, "DONE", "synced=1 skipped=0 errors=0 (dry run)"), (None, "BUSY", None)],
)
async def test_control_records_canary_outcome(
    temp_database: Path, outcome: TaskRunReport | None, state: str, text: str | None
) -> None:
    async def single(
        _settings: Settings,
        _target: CanaryTarget,
        *,
        force_live: bool = False,
        force_dry: bool = False,
    ) -> TaskRunReport | None:
        return outcome

    control = TaskSyncControl(get_settings(), single_runner=single)
    assert control.trigger_single(ORDER) is True
    assert control.trigger_single(ORDER) is False
    while control.busy:
        await asyncio.sleep(0.01)
    await get_engine().dispose()  # the canary read its DRY_RUN_TASK rows

    canary = control._canary
    assert canary is not None and canary.state == state and canary.summary == text


@pytest.mark.asyncio
async def test_forced_live_canary_bypasses_dry_run_for_that_run_only(
    backends: MockBackends,
) -> None:
    settings = get_settings()
    assert settings.sync_dry_run is True

    forced = await run_single(settings, ORDER, force_live=True)
    writes_after_forced = len(backends.writes)
    normal = await run_single(settings, ORDER)

    assert forced is not None and forced.dry_run is False
    assert writes_after_forced > 0 and len(backends.writes) == writes_after_forced
    assert normal is not None and normal.dry_run is True
    assert await audited_operations()  # only the follow-up dry run simulated writes
