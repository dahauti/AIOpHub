"""Shared UI test doubles: a canned dashboard snapshot and fake control ports."""

from datetime import UTC, datetime

from src.domain.canary import CanaryTarget
from src.schemas.audit_filter import AuditQuery
from src.schemas.schedule import ScheduleSettings
from src.schemas.sync import (
    AuditRecord,
    ConfigFlag,
    DashboardSnapshot,
    RuntimeConfig,
    SingleSyncResult,
)
from src.services.cockpit_models import CanaryOutcome, LastRun, TaskCockpit

NOW = datetime(2026, 10, 20, 12, 30, 5, tzinfo=UTC)
SNAPSHOT = DashboardSnapshot(
    status="IDLE",
    cursor_timestamp=datetime(2026, 10, 20, 12, 15, tzinfo=UTC),
    updated_at=NOW,
    processed=12,
    skipped=34,
    errors=2,
    recent=[
        AuditRecord(created_at=NOW, entity_key="SN-0001", action="UPSERT", status_code=201),
        AuditRecord(
            created_at=NOW,
            entity_key="SN-0002",
            action="FAILED",
            status_code=422,
            error_message="<script>alert(1)</script>",
        ),
    ],
)


class FakeControl:
    def __init__(self, snapshot: DashboardSnapshot, accept: bool = True) -> None:
        self._snapshot = snapshot
        self.accept = accept
        self.triggers = 0
        self.dry_run = False
        self.auto_sync = False
        self.mode_locked = False
        self.aborts = 0
        self.cursor_locked = False
        self.cursor_sets: list[datetime] = []
        self.schedule = ScheduleSettings()
        self.audit_queries: list[AuditQuery] = []
        self.next_run: datetime | None = None

    def trigger(self) -> bool:
        self.triggers += 1
        return self.accept

    async def snapshot(self) -> DashboardSnapshot:
        return self._snapshot

    async def set_cursor(self, cursor: datetime) -> bool:
        if self.cursor_locked:
            return False
        self.cursor_sets.append(cursor)
        self._snapshot = self._snapshot.model_copy(update={"cursor_timestamp": cursor})
        return True

    async def config(self) -> RuntimeConfig:
        return RuntimeConfig(
            dry_run=self.dry_run, auto_sync_enabled=self.auto_sync, **self.schedule.model_dump()
        )

    async def toggle(self, flag: ConfigFlag) -> RuntimeConfig | None:
        if flag == "dry_run":
            if self.mode_locked:
                return None
            self.dry_run = not self.dry_run
        else:
            self.auto_sync = not self.auto_sync
        return await self.config()

    async def update_schedule(self, schedule: ScheduleSettings) -> RuntimeConfig:
        self.schedule = schedule
        return await self.config()

    async def audit(self, query: AuditQuery) -> list[AuditRecord]:
        """In-memory filter over the snapshot rows (date range ignored)."""
        self.audit_queries.append(query)
        rows = self._snapshot.recent
        if query.actions is not None:
            rows = [row for row in rows if row.action in query.actions]
        if query.search is not None:
            rows = [row for row in rows if query.search.lower() in row.entity_key.lower()]
        return rows[: query.limit]

    async def next_sync(self) -> datetime | None:
        return self.next_run if self.auto_sync else None

    async def abort(self) -> bool:
        if self._snapshot.status != "RUNNING":
            return False
        self.aborts += 1
        self._snapshot = self._snapshot.model_copy(update={"status": "IDLE"})
        return True


class FakeSingleSync:
    def __init__(self, result: SingleSyncResult) -> None:
        self.result = result
        self.calls: list[tuple[str, bool]] = []

    async def sync_single_vehicle(
        self, serial_no: str, force_live: bool = False
    ) -> SingleSyncResult:
        self.calls.append((serial_no, force_live))
        return self.result.model_copy(update={"serial_no": serial_no})


LAST = LastRun(finished_at=NOW, dry_run=True, processed=7, synced=5, errors=2, elapsed_seconds=3.25)


class FakeTaskControl:
    def __init__(self) -> None:
        self.state = TaskCockpit(
            status="IDLE", updated_at=NOW, running=False, dry_run=True, app_env="dev", last_run=LAST
        )
        self.triggers = 0
        self.canaries: list[CanaryTarget] = []
        self.forced: list[bool] = []

    def trigger(self) -> bool:
        self.triggers += 1
        if self.state.running:
            return False
        self.state = self.state.model_copy(update={"running": True, "status": "RUNNING"})
        return True

    def trigger_single(self, target: CanaryTarget, force_live: bool = False) -> bool:
        self.canaries.append(target)
        self.forced.append(force_live)
        if not self.trigger():
            return False
        canary = CanaryOutcome(target=target, state="RUNNING")
        self.state = self.state.model_copy(update={"canary": canary})
        return True

    async def cockpit(self) -> TaskCockpit:
        return self.state

    async def toggle_dry_run(self) -> RuntimeConfig | None:
        if self.state.running:
            return None
        self.state = self.state.model_copy(update={"dry_run": not self.state.dry_run})
        return RuntimeConfig(dry_run=self.state.dry_run)
