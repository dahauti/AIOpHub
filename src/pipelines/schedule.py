"""Due-time arithmetic for the dashboard schedule (pure; no I/O)."""

from datetime import date, datetime, time, timedelta, tzinfo

from src.schemas.schedule import RunKind, ScheduleSettings


def _at(day: date, hhmm: str, tz: tzinfo | None) -> datetime:
    """`day` at HH:MM in `tz`; None = the server's local zone (DST-aware via the OS)."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    naive = datetime.combine(day, time(hour, minute))
    return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()


def next_incremental(schedule: ScheduleSettings, now: datetime, tz: tzinfo | None) -> datetime:
    """First incremental run strictly after `now`."""
    if schedule.schedule_mode == "interval":
        return now + timedelta(minutes=schedule.interval_minutes)
    today = now.astimezone(tz).date()
    candidate = _at(today, schedule.nightly_time, tz)
    if candidate > now:
        return candidate
    return _at(today + timedelta(days=1), schedule.nightly_time, tz)


def next_full(schedule: ScheduleSettings, now: datetime, tz: tzinfo | None) -> datetime | None:
    """First weekly full run strictly after `now`; None when the full sync is disabled."""
    if not schedule.full_sync_enabled:
        return None
    today = now.astimezone(tz).date()
    day = today + timedelta(days=(schedule.full_sync_weekday - today.weekday()) % 7)
    candidate = _at(day, schedule.full_sync_time, tz)
    if candidate > now:
        return candidate
    return _at(day + timedelta(days=7), schedule.full_sync_time, tz)


class ScheduleTracker:
    """Next due times for the current schedule.

    A changed schedule is re-planned from `now`, so editing it never fires a missed slot.
    A due run stays due until `started` is called (e.g. while another run is active).
    """

    def __init__(self, tz: tzinfo | None = None) -> None:
        self._tz = tz
        self._key: tuple[str, str, int, bool, int, str] | None = None
        self._next_incremental: datetime | None = None
        self._next_full: datetime | None = None

    def due(self, schedule: ScheduleSettings, now: datetime) -> RunKind | None:
        """The run due at `now` (a full run supersedes an incremental one), if any."""
        if schedule.key != self._key:
            self._key = schedule.key
            self._next_incremental = next_incremental(schedule, now, self._tz)
            self._next_full = next_full(schedule, now, self._tz)
        if self._next_full is not None and now >= self._next_full:
            return "full"
        if self._next_incremental is not None and now >= self._next_incremental:
            return "incremental"
        return None

    def reset(self) -> None:
        """Forget the plan; the next `due` call re-plans from its `now`."""
        self._key = None

    def started(self, kind: RunKind, schedule: ScheduleSettings, now: datetime) -> None:
        """Plan the next slots; a full run also covers the pending incremental one."""
        self._next_incremental = next_incremental(schedule, now, self._tz)
        if kind == "full":
            self._next_full = next_full(schedule, now, self._tz)
