"""`link_health` persistence: one row per external service, newest outcome wins."""

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.link_health import LinkOutcome
from src.models.audit import LinkHealth


class LinkHealthRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, outcomes: Mapping[str, LinkOutcome]) -> None:
        """Upsert each outcome unless the stored row is newer (another process wrote later)."""
        if not outcomes:
            return
        async with self._sessions.begin() as session:
            for outcome in outcomes.values():
                values = outcome.model_dump()
                statement = insert(LinkHealth).values(values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[LinkHealth.service],
                        set_={k: v for k, v in values.items() if k != "service"},
                        where=LinkHealth.checked_at < outcome.checked_at,
                    )
                )

    async def load(self) -> dict[str, LinkOutcome]:
        async with self._sessions() as session:
            rows = (await session.scalars(select(LinkHealth))).all()
        return {
            row.service: LinkOutcome(
                service=row.service,
                status_code=row.status_code,
                detail=row.detail,
                checked_at=row.checked_at,
            )
            for row in rows
        }
