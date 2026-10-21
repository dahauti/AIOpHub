"""Schedule arithmetic: nightly / interval slots, weekly full sync, and the due-time tracker."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.pipelines.schedule import ScheduleTracker, next_full, next_incremental
from src.schemas.schedule import ScheduleSettings

TUESDAY_NOON = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
NIGHTLY = ScheduleSettings(nightly_time="02:00")


@pytest.mark.parametrize(
    ("nightly_time", "expected"),
    [
        ("13:30", datetime(2026, 10, 20, 13, 30, tzinfo=UTC)),
        ("12:00", datetime(2026, 10, 21, 12, 0, tzinfo=UTC)),
        ("02:00", datetime(2026, 10, 21, 2, 0, tzinfo=UTC)),
    ],
)
def test_nightly_slot_is_the_next_occurrence_strictly_after_now(
    nightly_time: str, expected: datetime
) -> None:
    schedule = ScheduleSettings(nightly_time=nightly_time)

    assert next_incremental(schedule, TUESDAY_NOON, UTC) == expected


def test_interval_slot_is_now_plus_interval() -> None:
    schedule = ScheduleSettings(schedule_mode="interval", interval_minutes=45)

    assert next_incremental(schedule, TUESDAY_NOON, UTC) == TUESDAY_NOON + timedelta(minutes=45)


@pytest.mark.parametrize(
    ("weekday", "time", "expected"),
    [
        (1, "18:00", datetime(2026, 10, 20, 18, 0, tzinfo=UTC)),
        (1, "02:00", datetime(2026, 10, 27, 2, 0, tzinfo=UTC)),
        (6, "02:00", datetime(2026, 10, 25, 2, 0, tzinfo=UTC)),
        (0, "02:00", datetime(2026, 10, 26, 2, 0, tzinfo=UTC)),
    ],
)
def test_weekly_full_slot(weekday: int, time: str, expected: datetime) -> None:
    schedule = ScheduleSettings(
        full_sync_enabled=True, full_sync_weekday=weekday, full_sync_time=time
    )

    assert next_full(schedule, TUESDAY_NOON, UTC) == expected


def test_disabled_full_sync_has_no_slot() -> None:
    assert next_full(ScheduleSettings(full_sync_enabled=False), TUESDAY_NOON, UTC) is None


def test_server_local_zone_yields_aware_datetimes() -> None:
    slot = next_incremental(NIGHTLY, TUESDAY_NOON, None)

    assert slot.tzinfo is not None and slot > TUESDAY_NOON


@pytest.mark.parametrize(
    "field",
    [
        {"nightly_time": "24:00"},
        {"full_sync_time": "2:00"},
        {"interval_minutes": 0},
        {"full_sync_weekday": 7},
        {"schedule_mode": "hourly"},
    ],
)
def test_invalid_schedule_values_are_rejected(field: dict[str, str | int]) -> None:
    with pytest.raises(ValidationError):
        ScheduleSettings.model_validate(field)


def test_tracker_is_due_once_per_slot_and_waits_until_started() -> None:
    tracker = ScheduleTracker(UTC)
    slot = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)

    assert tracker.due(NIGHTLY, TUESDAY_NOON) is None
    assert tracker.due(NIGHTLY, slot) == "incremental"
    assert tracker.due(NIGHTLY, slot + timedelta(minutes=5)) == "incremental"  # still pending
    tracker.started("incremental", NIGHTLY, slot + timedelta(minutes=5))
    assert tracker.due(NIGHTLY, slot + timedelta(hours=1)) is None


def test_changed_schedule_is_replanned_from_now_without_firing_a_missed_slot() -> None:
    tracker = ScheduleTracker(UTC)
    tracker.due(NIGHTLY, TUESDAY_NOON)
    earlier = ScheduleSettings(nightly_time="09:00")

    assert tracker.due(earlier, TUESDAY_NOON) is None
    assert tracker.due(earlier, datetime(2026, 10, 21, 9, 0, tzinfo=UTC)) == "incremental"


def test_full_run_supersedes_and_also_advances_the_incremental_slot() -> None:
    schedule = ScheduleSettings(
        nightly_time="02:00", full_sync_enabled=True, full_sync_weekday=2, full_sync_time="02:00"
    )
    tracker = ScheduleTracker(UTC)
    wednesday = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)
    tracker.due(schedule, TUESDAY_NOON)

    assert tracker.due(schedule, wednesday) == "full"
    tracker.started("full", schedule, wednesday)
    assert tracker.due(schedule, wednesday + timedelta(minutes=1)) is None
    assert tracker.due(schedule, wednesday + timedelta(days=1)) == "incremental"


def test_reset_forgets_the_plan() -> None:
    tracker = ScheduleTracker(UTC)
    tracker.due(NIGHTLY, TUESDAY_NOON)
    tracker.reset()

    assert tracker.due(NIGHTLY, datetime(2026, 10, 22, 12, 0, tzinfo=UTC)) is None


def test_describe_summarises_cadence_and_full_sync() -> None:
    weekly = ScheduleSettings(
        schedule_mode="interval", interval_minutes=30, full_sync_enabled=True
    )

    assert NIGHTLY.describe() == "Incremental sync nightly at 02:00; weekly full sync off"
    assert weekly.describe() == "Incremental sync every 30 min; full sync Sunday at 02:00"
