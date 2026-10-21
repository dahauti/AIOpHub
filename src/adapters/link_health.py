"""Process-wide record of the latest HTTP outcome per external service (BC, Entra, Frontu).

`send_with_retry` reports every finished call here, so link health is tracked per service and
never inferred from stream results. Runs persist a snapshot (`link_health` table) so outcomes
from CLI processes reach the dashboard process.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

# Responses that mean the link itself is unusable (auth, throttled out, server/gateway down).
LINK_FAILURE_CODES = frozenset({401, 403, 429})
MAX_DETAIL_CHARS = 300


class LinkOutcome(BaseModel):
    """Latest call result for one service; `status_code` None means no HTTP response at all."""

    model_config = ConfigDict(frozen=True)

    service: str
    status_code: int | None
    detail: str | None = None
    checked_at: datetime

    @property
    def reachable(self) -> bool:
        return self.status_code is not None

    @property
    def ok(self) -> bool:
        """A business 4xx (bad filter, not found) still proves the link works."""
        code = self.status_code
        return code is not None and code < 500 and code not in LINK_FAILURE_CODES


class LinkHealthRegistry:
    def __init__(self) -> None:
        self._latest: dict[str, LinkOutcome] = {}

    def record(self, service: str, status_code: int | None, detail: str | None = None) -> None:
        self._latest[service] = LinkOutcome(
            service=service,
            status_code=status_code,
            detail=detail[:MAX_DETAIL_CHARS] if detail else None,
            checked_at=datetime.now(UTC),
        )

    def snapshot(self) -> dict[str, LinkOutcome]:
        return dict(self._latest)

    def clear(self) -> None:
        self._latest.clear()


LINK_HEALTH = LinkHealthRegistry()
