"""Service ports (adapter protocols), run outcomes and the run summary."""

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, JsonValue

from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.domain.frontu_models import FrontuMaterial, FrontuTask, FrontuTaskPayload, FrontuWork
from src.schemas.operations import OutcomeResult

Outcome = Literal["SYNCED", "SKIPPED", "FAILED", "VALIDATION_ERROR"]
# Upper bound for error text persisted to `task_sync_state.error_message`.
MAX_ERROR_CHARS = 500


class BCTasksPort(Protocol):
    async def fetch_released_orders(self, watermark: str) -> list[BCServiceOrderHeader]: ...
    async def fetch_order(self, order_no: str) -> BCServiceOrderHeader | None: ...
    async def fetch_order_lines(self, order_no: str) -> list[BCServiceOrderLine]: ...
    async def update_order_header(
        self, order_no: str, patch_data: dict[str, JsonValue], etag: str | None = None
    ) -> None: ...
    async def update_order_line(
        self,
        order_no: str,
        line_no: int,
        patch_data: dict[str, JsonValue],
        etag: str | None = None,
    ) -> None: ...
    async def create_service_order(self, header: BCServiceOrderHeader) -> str: ...
    async def create_order_line(self, line: BCServiceOrderLine) -> None: ...


class FrontuTasksPort(Protocol):
    async def fetch_task_delta(self, watermark: int) -> list[FrontuTask]: ...
    async def scan_latest_version(self, watermark: int) -> int | None: ...
    async def get_task(self, task_guid: str) -> FrontuTask | None: ...
    async def upsert_task(self, task: FrontuTaskPayload) -> FrontuTask: ...
    async def fetch_task_materials(self, task_guid: str) -> list[FrontuMaterial]: ...
    async def fetch_task_works(self, task_guid: str) -> list[FrontuWork]: ...
    async def push_material(self, material: FrontuMaterial) -> str: ...
    async def push_work(self, work: FrontuWork) -> str: ...
    async def find_client_guid(self, system_id: str) -> str | None: ...
    async def find_workspace_guid(self, system_id: str) -> str | None: ...
    async def find_task_type_guid(self, system_id: str) -> str | None: ...
    async def get_client_system_id(self, client_guid: str) -> str | None: ...


class OutcomeSink(Protocol):
    """Per-record outcome of the job run in progress (`RunRecorder`); never raises."""

    async def record_outcome(
        self,
        object_type: str,
        object_id: str,
        result: OutcomeResult,
        *,
        error_code: str | None = None,
        params: dict[str, str] | None = None,
        error: BaseException | None = None,
    ) -> None: ...


class SyncRunResult(BaseModel):
    """Per-outcome counts for one service run."""

    synced: int = 0
    skipped: int = 0
    failed: int = 0
    validation_errors: int = 0

    @property
    def processed(self) -> int:
        return self.synced + self.skipped + self.failed + self.validation_errors

    @property
    def errors(self) -> int:
        return self.failed + self.validation_errors

    def record(self, outcome: Outcome) -> None:
        if outcome == "SYNCED":
            self.synced += 1
        elif outcome == "SKIPPED":
            self.skipped += 1
        elif outcome == "FAILED":
            self.failed += 1
        else:
            self.validation_errors += 1


def utc_now() -> datetime:
    return datetime.now(UTC)
