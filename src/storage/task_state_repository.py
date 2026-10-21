"""CRUD for `task_sync_state` and `task_line_sync_state` (tables created by TaskRepository)."""

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from src.domain.task_state import TaskLineSyncState, TaskSyncState

_TASK_COLUMNS = (
    "service_order_no, frontu_task_guid, frontu_task_code, header_hash, origin_system,"
    " sync_status, error_message"
)
_SELECT_TASK = f"SELECT {_TASK_COLUMNS} FROM task_sync_state WHERE "
_UPSERT_TASK = text(
    f"INSERT INTO task_sync_state ({_TASK_COLUMNS}) VALUES (:service_order_no,"
    " :frontu_task_guid, :frontu_task_code, :header_hash, :origin_system, :sync_status,"
    " :error_message) ON CONFLICT(service_order_no) DO UPDATE SET"
    " frontu_task_guid = excluded.frontu_task_guid,"
    " frontu_task_code = excluded.frontu_task_code, header_hash = excluded.header_hash,"
    " origin_system = excluded.origin_system, sync_status = excluded.sync_status,"
    " error_message = excluded.error_message, updated_at = CURRENT_TIMESTAMP"
)
_DELETE_TASK = text("DELETE FROM task_sync_state WHERE service_order_no = :order_no")
_LINE_COLUMNS = "service_order_no, line_no, frontu_entity_guid, entity_type, line_hash, sync_status"
_SELECT_LINES = text(
    f"SELECT {_LINE_COLUMNS} FROM task_line_sync_state WHERE service_order_no = :order_no"
    " ORDER BY line_no"
)
_UPSERT_LINE = text(
    f"INSERT INTO task_line_sync_state ({_LINE_COLUMNS}) VALUES (:service_order_no, :line_no,"
    " :frontu_entity_guid, :entity_type, :line_hash, :sync_status)"
    " ON CONFLICT(service_order_no, line_no, entity_type) DO UPDATE SET"
    " frontu_entity_guid = excluded.frontu_entity_guid, line_hash = excluded.line_hash,"
    " sync_status = excluded.sync_status, updated_at = CURRENT_TIMESTAMP"
)


class TaskStateRepository:
    """Task and line link registries; every call runs in its own short transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_by_order(self, order_no: str) -> TaskSyncState | None:
        return await self._select_task("service_order_no = :key", order_no)

    async def get_by_task_guid(self, task_guid: str) -> TaskSyncState | None:
        return await self._select_task("frontu_task_guid = :key", task_guid)

    async def save_task(self, state: TaskSyncState) -> None:
        """Upsert by Service Order No."""
        async with self._engine.begin() as connection:
            await connection.execute(_UPSERT_TASK, state.model_dump())

    async def delete_task(self, order_no: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(_DELETE_TASK, {"order_no": order_no})

    async def get_lines(self, order_no: str) -> list[TaskLineSyncState]:
        async with self._engine.connect() as connection:
            result = await connection.execute(_SELECT_LINES, {"order_no": order_no})
            rows = result.mappings().all()
        return [TaskLineSyncState.model_validate(dict(row)) for row in rows]

    async def save_line(self, state: TaskLineSyncState) -> None:
        """Upsert by (Service Order No, line No, entity type)."""
        async with self._engine.begin() as connection:
            await connection.execute(_UPSERT_LINE, state.model_dump())

    async def _select_task(self, condition: str, key: str) -> TaskSyncState | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(text(_SELECT_TASK + condition), {"key": key})
            row: RowMapping | None = result.mappings().first()
        return None if row is None else TaskSyncState.model_validate(dict(row))
