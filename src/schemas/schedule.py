"""Dashboard schedule settings: incremental cadence plus the optional weekly full sync."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScheduleMode = Literal["nightly", "interval"]
RunKind = Literal["incremental", "full"]
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class ScheduleSettings(BaseModel):
    """Typed `sync_config` schedule columns; times are HH:MM in the server's local time zone."""

    model_config = ConfigDict(from_attributes=True, frozen=True, extra="ignore")

    schedule_mode: ScheduleMode = "nightly"
    nightly_time: str = Field(default="02:00", pattern=HHMM)
    interval_minutes: int = Field(default=60, ge=5, le=1440)
    full_sync_enabled: bool = False
    full_sync_weekday: int = Field(default=6, ge=0, le=6)  # 0 = Monday ... 6 = Sunday
    full_sync_time: str = Field(default="02:00", pattern=HHMM)

    @property
    def key(self) -> tuple[str, str, int, bool, int, str]:
        """Comparable identity of the schedule (excludes the run-mode flags of subclasses)."""
        return (
            self.schedule_mode,
            self.nightly_time,
            self.interval_minutes,
            self.full_sync_enabled,
            self.full_sync_weekday,
            self.full_sync_time,
        )

    def describe(self) -> str:
        """Human-readable summary for the dashboard banner."""
        if self.schedule_mode == "nightly":
            cadence = f"nightly at {self.nightly_time}"
        else:
            cadence = f"every {self.interval_minutes} min"
        if not self.full_sync_enabled:
            return f"Incremental sync {cadence}; weekly full sync off"
        weekday = WEEKDAYS[self.full_sync_weekday]
        return f"Incremental sync {cadence}; full sync {weekday} at {self.full_sync_time}"
