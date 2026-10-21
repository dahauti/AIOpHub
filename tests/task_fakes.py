"""In-memory BC and Frontu task ports for Module 2 service tests."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import create_async_engine

from src.adapters.resilience import ExternalServiceError
from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.domain.frontu_models import FrontuMaterial, FrontuTask, FrontuTaskPayload, FrontuWork
from src.storage.task_repository import TaskRepository
from src.storage.task_state_repository import TaskStateRepository

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


class FakeBC:
    def __init__(self) -> None:
        self.orders: dict[str, BCServiceOrderHeader] = {}
        self.lines: dict[str, list[BCServiceOrderLine]] = {}
        self.released: list[BCServiceOrderHeader] = []
        self.watermarks: list[str] = []
        self.header_patches: list[tuple[str, dict[str, JsonValue]]] = []
        self.line_patches: list[tuple[str, int, dict[str, JsonValue]]] = []
        self.created_orders: list[BCServiceOrderHeader] = []
        self.created_lines: list[BCServiceOrderLine] = []
        self.fail_patch_for: set[str] = set()

    async def fetch_released_orders(self, watermark: str) -> list[BCServiceOrderHeader]:
        self.watermarks.append(watermark)
        return self.released

    async def fetch_order(self, order_no: str) -> BCServiceOrderHeader | None:
        return self.orders.get(order_no)

    async def fetch_order_lines(self, order_no: str) -> list[BCServiceOrderLine]:
        return list(self.lines.get(order_no, []))

    async def update_order_header(
        self, order_no: str, patch_data: dict[str, JsonValue], etag: str | None = None
    ) -> None:
        if order_no in self.fail_patch_for:
            raise ExternalServiceError("business-central", 503, "gave up")
        self.header_patches.append((order_no, patch_data))

    async def update_order_line(
        self,
        order_no: str,
        line_no: int,
        patch_data: dict[str, JsonValue],
        etag: str | None = None,
    ) -> None:
        self.line_patches.append((order_no, line_no, patch_data))

    async def create_service_order(self, header: BCServiceOrderHeader) -> str:
        self.created_orders.append(header)
        order_no = f"SO{90000 + len(self.created_orders)}"
        self.orders[order_no] = header.model_copy(update={"No": order_no})
        return order_no

    async def create_order_line(self, line: BCServiceOrderLine) -> None:
        self.created_lines.append(line)
        self.lines.setdefault(line.DocumentNo, []).append(line)


class FakeFrontu:
    def __init__(self) -> None:
        self.delta: list[FrontuTask] = []
        self.versions: list[int] = []
        self.scans: list[int] = []
        self.upserts: list[FrontuTaskPayload] = []
        self.materials: dict[str, list[FrontuMaterial]] = {}
        self.works: dict[str, list[FrontuWork]] = {}
        self.pushed_materials: list[FrontuMaterial] = []
        self.pushed_works: list[FrontuWork] = []
        self.clients: dict[str, str] = {"C00010": "client-guid"}
        self.workspaces: dict[str, str] = {"SN-1": "ws-guid"}
        self.task_types: dict[str, str] = {"SERVICE": "type-guid"}
        self.client_customers: dict[str, str] = {"client-guid": "C00010"}

    async def fetch_task_delta(self, watermark: int) -> list[FrontuTask]:
        self.versions.append(watermark)
        return self.delta

    async def scan_latest_version(self, watermark: int) -> int | None:
        self.scans.append(watermark)
        versions = [task.version for task in self.delta if task.version is not None]
        return max(versions, default=None)

    async def get_task(self, task_guid: str) -> FrontuTask | None:
        return next((task for task in self.delta if task.guid == task_guid), None)

    async def upsert_task(self, task: FrontuTaskPayload) -> FrontuTask:
        self.upserts.append(task)
        guid = task.guid or f"task-{task.systemId}"
        return FrontuTask(guid=guid, code="T-100", systemId=task.systemId)

    async def fetch_task_materials(self, task_guid: str) -> list[FrontuMaterial]:
        return self.materials.get(task_guid, [])

    async def fetch_task_works(self, task_guid: str) -> list[FrontuWork]:
        return self.works.get(task_guid, [])

    async def push_material(self, material: FrontuMaterial) -> str:
        self.pushed_materials.append(material)
        return f"mat-{len(self.pushed_materials)}"

    async def push_work(self, work: FrontuWork) -> str:
        self.pushed_works.append(work)
        return f"work-{len(self.pushed_works)}"

    async def find_client_guid(self, system_id: str) -> str | None:
        return self.clients.get(system_id)

    async def find_workspace_guid(self, system_id: str) -> str | None:
        return self.workspaces.get(system_id)

    async def find_task_type_guid(self, system_id: str) -> str | None:
        return self.task_types.get(system_id)

    async def get_client_system_id(self, client_guid: str) -> str | None:
        return self.client_customers.get(client_guid)


@dataclass
class Stores:
    watermarks: TaskRepository
    states: TaskStateRepository


@pytest_asyncio.fixture
async def stores(tmp_path: Path) -> AsyncIterator[Stores]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'state.db').as_posix()}")
    watermarks = TaskRepository(engine)
    await watermarks.migrate()
    yield Stores(watermarks, TaskStateRepository(engine))
    await engine.dispose()
