"""In-memory sync repository mirroring the SQL semantics (atomic claim, forward-only cursor)."""

from collections.abc import Sequence
from datetime import datetime

from src.repositories.sync_statements import STALE_RUN_AFTER
from src.schemas.sync import (
    RUNNING,
    AuditEntry,
    ConfigFlag,
    DashboardSnapshot,
    ReleaseStatus,
    RuntimeConfig,
)


class FakeSyncRepository:
    """Mirrors the SQL repository semantics (atomic claim, forward-only cursor)."""

    def __init__(
        self,
        cursor: datetime | None = None,
        status: str = "IDLE",
        updated_at: datetime | None = None,
    ) -> None:
        self.cursor = cursor
        self.status = status
        self.updated_at = updated_at
        self.fingerprints: dict[str, str] = {}
        self.audit: list[AuditEntry] = []
        self.statuses: list[str] = []
        self.heartbeats = 0

    async def try_acquire(self, now: datetime, initial_cursor: datetime) -> bool:
        if self.cursor is None:
            self.cursor = initial_cursor
        elif (
            self.status == RUNNING
            and self.updated_at is not None
            and self.updated_at >= now - STALE_RUN_AFTER
        ):
            return False
        self.status, self.updated_at = RUNNING, now
        self.statuses.append(RUNNING)
        return True

    async def heartbeat(self, now: datetime) -> None:
        self.heartbeats += 1
        self.updated_at = now

    async def release(self, status: ReleaseStatus, now: datetime) -> None:
        self.status, self.updated_at = status, now
        self.statuses.append(status)

    async def get_cursor(self) -> datetime:
        assert self.cursor is not None
        return self.cursor

    async def advance_cursor(self, cursor: datetime, now: datetime) -> None:
        if self.cursor is None or cursor > self.cursor:
            self.cursor = cursor

    async def set_cursor(self, cursor: datetime, now: datetime) -> bool:
        if self.status == RUNNING:
            return False
        self.cursor = cursor
        return True

    async def get_fingerprints(self, keys: Sequence[str]) -> dict[str, str]:
        return {key: self.fingerprints[key] for key in keys if key in self.fingerprints}

    async def save_fingerprint(self, key: str, payload_hash: str, now: datetime) -> None:
        self.fingerprints[key] = payload_hash

    async def delete_fingerprint(self, key: str) -> None:
        self.fingerprints.pop(key, None)

    async def record_audit(self, entry: AuditEntry) -> None:
        self.audit.append(entry)

    async def dashboard_snapshot(self, since: datetime, limit: int) -> DashboardSnapshot:
        return DashboardSnapshot(status=self.status, cursor_timestamp=self.cursor)


class FakeConfigStore:
    """In-memory runtime config; `toggle` flips one flag like the SQL store."""

    def __init__(self, dry_run: bool = True, auto_sync_enabled: bool = False) -> None:
        self.config = RuntimeConfig(dry_run=dry_run, auto_sync_enabled=auto_sync_enabled)
        self.toggles: list[ConfigFlag] = []

    async def get(self) -> RuntimeConfig:
        return self.config

    async def toggle(self, flag: ConfigFlag) -> RuntimeConfig:
        self.toggles.append(flag)
        current = self.config.model_dump()
        self.config = RuntimeConfig.model_validate({**current, flag: not current[flag]})
        return self.config
