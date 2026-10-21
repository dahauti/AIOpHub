"""Module 2 SQLite state: task/line sync registries and isolated stream watermarks."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

DEFAULT_SAFETY_BUFFER_SECONDS = 900

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS task_sync_state (
        service_order_no TEXT PRIMARY KEY,
        frontu_task_guid TEXT UNIQUE NOT NULL,
        frontu_task_code TEXT,
        header_hash TEXT NOT NULL,
        origin_system TEXT CHECK(origin_system IN ('BC', 'FRONTU')) NOT NULL,
        sync_status TEXT CHECK(sync_status IN ('SYNCED', 'PENDING', 'FAILED', 'VALIDATION_ERROR')),
        error_message TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_line_sync_state (
        service_order_no TEXT NOT NULL,
        line_no INTEGER NOT NULL,
        frontu_entity_guid TEXT NOT NULL,
        entity_type TEXT CHECK(entity_type IN ('MATERIAL', 'WORK', 'COMMENT')),
        line_hash TEXT NOT NULL,
        sync_status TEXT CHECK(sync_status IN ('SYNCED', 'FAILED')),
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (service_order_no, line_no, entity_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_watermarks (
        stream_name TEXT PRIMARY KEY,
        watermark_value TEXT NOT NULL,
        safety_buffer_seconds INTEGER DEFAULT 900,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
)

_SELECT_WATERMARK = text(
    "SELECT watermark_value, safety_buffer_seconds FROM sync_watermarks WHERE stream_name = :stream"
)
_UPSERT_WATERMARK = text(
    "INSERT INTO sync_watermarks (stream_name, watermark_value, safety_buffer_seconds)"
    " VALUES (:stream, :value, :buffer)"
    " ON CONFLICT(stream_name) DO UPDATE SET"
    " watermark_value = excluded.watermark_value,"
    " safety_buffer_seconds = COALESCE(:override, sync_watermarks.safety_buffer_seconds),"
    " updated_at = CURRENT_TIMESTAMP"
)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("watermark must be timezone-aware")
    return value.astimezone(UTC)


class TaskRepository:
    """SQLite store for Module 2; every call runs in its own short transaction."""

    def __init__(
        self,
        engine: AsyncEngine,
        default_safety_buffer_seconds: int = DEFAULT_SAFETY_BUFFER_SECONDS,
    ) -> None:
        if default_safety_buffer_seconds < 0:
            raise ValueError("safety buffer must be non-negative")
        self._engine = engine
        self._default_buffer = default_safety_buffer_seconds

    async def migrate(self) -> None:
        """Create the Module 2 tables if missing (idempotent)."""
        async with self._engine.begin() as connection:
            for ddl in MIGRATIONS:
                await connection.execute(text(ddl))

    async def get_raw_watermark(self, stream: str) -> datetime | None:
        """Stored watermark for a stream, without the safety buffer applied."""
        row = await self._select_watermark(stream)
        return None if row is None else row[0]

    async def get_watermark(self, stream: str) -> datetime | None:
        """Stored watermark minus the stream's `safety_buffer_seconds`; None when unset."""
        row = await self._select_watermark(stream)
        if row is None:
            return None
        value, buffer_seconds = row
        return value - timedelta(seconds=buffer_seconds)

    async def set_watermark(
        self, stream: str, value: datetime, safety_buffer_seconds: int | None = None
    ) -> None:
        """Upsert a stream watermark; the buffer is kept unless explicitly overridden."""
        await self._upsert(stream, _to_utc(value).isoformat(), safety_buffer_seconds)

    async def get_version_watermark(self, stream: str) -> int | None:
        """Stored Frontu `version` watermark; no time buffer applies to a version counter."""
        row = await self._select_raw(stream)
        return None if row is None else int(row[0])

    async def set_version_watermark(self, stream: str, version: int) -> None:
        await self._upsert(stream, str(version), None)

    async def _upsert(self, stream: str, value: str, safety_buffer_seconds: int | None) -> None:
        if safety_buffer_seconds is not None and safety_buffer_seconds < 0:
            raise ValueError("safety buffer must be non-negative")
        params: dict[str, str | int | None] = {
            "stream": stream,
            "value": value,
            "buffer": (
                self._default_buffer if safety_buffer_seconds is None else safety_buffer_seconds
            ),
            "override": safety_buffer_seconds,
        }
        async with self._engine.begin() as connection:
            await connection.execute(_UPSERT_WATERMARK, params)

    async def _select_watermark(self, stream: str) -> tuple[datetime, int] | None:
        row = await self._select_raw(stream)
        if row is None:
            return None
        raw_value, buffer_seconds = row
        return _to_utc(datetime.fromisoformat(raw_value)), buffer_seconds

    async def _select_raw(self, stream: str) -> tuple[str, int] | None:
        async with self._engine.connect() as connection:
            row = (await connection.execute(_SELECT_WATERMARK, {"stream": stream})).first()
        if row is None:
            return None
        raw_value: str = row[0]
        raw_buffer: int | None = row[1]
        return raw_value, self._default_buffer if raw_buffer is None else raw_buffer
