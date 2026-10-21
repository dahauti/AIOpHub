"""Dry-run decorators: reads pass through; BC/Frontu writes and local state writes are simulated.

Every simulated mutation is appended to `sync_audit_log` as a `DRY_RUN_TASK` entry whose
payload names the operation, so a dry run shows exactly what a live run would send.
"""

import itertools
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, JsonValue

from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.domain.frontu_models import FrontuMaterial, FrontuTask, FrontuTaskPayload, FrontuWork
from src.domain.task_state import TaskLineSyncState, TaskSyncState
from src.schemas.sync import AuditEntry
from src.services.ports import BCTasksPort, FrontuTasksPort
from src.storage.task_repository import TaskRepository
from src.storage.task_state_repository import TaskStateRepository

DRY_RUN_PREFIX = "DRY-RUN-"


class AuditSink(Protocol):
    async def record_audit(self, entry: AuditEntry) -> None: ...


class _Recorder:
    def __init__(self, audit: AuditSink) -> None:
        self._audit = audit
        self._ids = itertools.count(1)

    def simulated_id(self, kind: str) -> str:
        return f"{DRY_RUN_PREFIX}{kind}-{next(self._ids)}"

    async def record(self, entity_key: str, operation: str, body: JsonValue) -> None:
        payload: dict[str, JsonValue] = {"operation": operation, "body": body}
        await self._audit.record_audit(
            AuditEntry(entity_key=entity_key, action="DRY_RUN_TASK", payload=payload)
        )


def _json(model: BaseModel) -> dict[str, JsonValue]:
    return model.model_dump(mode="json", exclude_none=True)


class DryRunBC(_Recorder):
    """BCTasksPort that reads from BC and records, instead of sends, every write."""

    def __init__(self, inner: BCTasksPort, audit: AuditSink) -> None:
        super().__init__(audit)
        self._inner = inner

    async def fetch_released_orders(self, watermark: str) -> list[BCServiceOrderHeader]:
        return await self._inner.fetch_released_orders(watermark)

    async def fetch_order(self, order_no: str) -> BCServiceOrderHeader | None:
        return await self._inner.fetch_order(order_no)

    async def fetch_order_lines(self, order_no: str) -> list[BCServiceOrderLine]:
        return await self._inner.fetch_order_lines(order_no)

    async def update_order_header(
        self, order_no: str, patch_data: dict[str, JsonValue], etag: str | None = None
    ) -> None:
        await self.record(order_no, "bc.update_order_header", patch_data)

    async def update_order_line(
        self,
        order_no: str,
        line_no: int,
        patch_data: dict[str, JsonValue],
        etag: str | None = None,
    ) -> None:
        await self.record(f"{order_no}/{line_no}", "bc.update_order_line", patch_data)

    async def create_service_order(self, header: BCServiceOrderHeader) -> str:
        order_no = self.simulated_id("SO")
        await self.record(order_no, "bc.create_service_order", _json(header))
        return order_no

    async def create_order_line(self, line: BCServiceOrderLine) -> None:
        key = f"{line.DocumentNo}/{line.LineNo}"
        await self.record(key, "bc.create_order_line", _json(line))


class DryRunFrontu(_Recorder):
    """FrontuTasksPort that reads from Frontu and records, instead of sends, every write."""

    def __init__(self, inner: FrontuTasksPort, audit: AuditSink) -> None:
        super().__init__(audit)
        self._inner = inner

    async def fetch_task_delta(self, watermark: int) -> list[FrontuTask]:
        return await self._inner.fetch_task_delta(watermark)

    async def scan_latest_version(self, watermark: int) -> int | None:
        return await self._inner.scan_latest_version(watermark)

    async def get_task(self, task_guid: str) -> FrontuTask | None:
        return await self._inner.get_task(task_guid)

    async def fetch_task_materials(self, task_guid: str) -> list[FrontuMaterial]:
        return await self._inner.fetch_task_materials(task_guid)

    async def fetch_task_works(self, task_guid: str) -> list[FrontuWork]:
        return await self._inner.fetch_task_works(task_guid)

    async def find_client_guid(self, system_id: str) -> str | None:
        return await self._inner.find_client_guid(system_id)

    async def find_workspace_guid(self, system_id: str) -> str | None:
        return await self._inner.find_workspace_guid(system_id)

    async def find_task_type_guid(self, system_id: str) -> str | None:
        return await self._inner.find_task_type_guid(system_id)

    async def get_client_system_id(self, client_guid: str) -> str | None:
        return await self._inner.get_client_system_id(client_guid)

    async def upsert_task(self, task: FrontuTaskPayload) -> FrontuTask:
        guid = task.guid or self.simulated_id("task")
        await self.record(guid, "frontu.upsert_task", _json(task))
        return FrontuTask(guid=guid, systemId=task.systemId, title=task.title)

    async def push_material(self, material: FrontuMaterial) -> str:
        await self.record(material.task, "frontu.push_material", _json(material))
        return self.simulated_id("material")

    async def push_work(self, work: FrontuWork) -> str:
        await self.record(work.task, "frontu.push_work", _json(work))
        return self.simulated_id("work")


class ReadOnlyTaskRepository(TaskRepository):
    """Watermarks stay put so the next live run still sees every record of this dry run."""

    async def set_watermark(
        self, stream: str, value: datetime, safety_buffer_seconds: int | None = None
    ) -> None:
        return None

    async def set_version_watermark(self, stream: str, version: int) -> None:
        return None


class ReadOnlyStateRepository(TaskStateRepository):
    """Simulated GUIDs and order numbers never reach the link registries."""

    async def save_task(self, state: TaskSyncState) -> None:
        return None

    async def delete_task(self, order_no: str) -> None:
        return None

    async def save_line(self, state: TaskLineSyncState) -> None:
        return None
