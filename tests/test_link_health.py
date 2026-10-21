"""Per-service link health: recording in send_with_retry, panel rules, merge and persistence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from src.adapters.link_health import LINK_HEALTH, LinkOutcome
from src.adapters.resilience import ExternalServiceError, send_with_retry
from src.database import get_engine, get_sessionmaker, init_db
from src.repositories.link_health_repository import LinkHealthRepository
from src.services.link_status import build_panel, merge

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _o(service: str, code: int | None, minutes: int = 0) -> LinkOutcome:
    return LinkOutcome(
        service=service, status_code=code, checked_at=T0 + timedelta(minutes=minutes)
    )


def test_frontu_failure_leaves_bc_and_gateway_green() -> None:
    panel = build_panel(
        {"business-central": _o("business-central", 200), "frontu": _o("frontu", 503)}
    )

    assert (panel.bc.health, panel.gateway.health, panel.frontu.health) == ("ok", "ok", "failing")


@pytest.mark.parametrize(
    ("code", "bc", "gateway"),
    [
        (404, "ok", "ok"),
        (400, "ok", "ok"),
        (401, "failing", "ok"),
        (502, "failing", "ok"),
        (None, "failing", "failing"),
    ],
)
def test_bc_and_gateway_rules(code: int | None, bc: str, gateway: str) -> None:
    panel = build_panel({"business-central": _o("business-central", code)})

    assert (panel.bc.health, panel.gateway.health, panel.frontu.health) == (bc, gateway, "unknown")


def test_newer_entra_token_failure_marks_bc_failing() -> None:
    outcomes = {"business-central": _o("business-central", 200), "entra-id": _o("entra-id", 401, 1)}

    assert build_panel(outcomes).bc.health == "failing"
    assert build_panel(outcomes).gateway.health == "ok"


def test_merge_keeps_the_newer_outcome_per_service() -> None:
    stored = {"frontu": _o("frontu", 503, 5), "business-central": _o("business-central", 200)}
    live = {"frontu": _o("frontu", 200, 1), "business-central": _o("business-central", 502, 2)}

    merged = merge(stored, live)

    assert (merged["frontu"].status_code, merged["business-central"].status_code) == (503, 502)


@pytest.mark.asyncio
async def test_send_with_retry_records_success_and_failure() -> None:
    LINK_HEALTH.clear()
    codes = iter([200, 401])
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(next(codes))))
    request = http.build_request("GET", "https://x.test/a")

    await send_with_retry(http, request, service="frontu")
    assert LINK_HEALTH.snapshot()["frontu"].status_code == 200
    with pytest.raises(ExternalServiceError):
        await send_with_retry(http, http.build_request("GET", "https://x.test/b"), service="frontu")

    assert LINK_HEALTH.snapshot()["frontu"].status_code == 401
    assert build_panel(LINK_HEALTH.snapshot()).frontu.health == "failing"
    LINK_HEALTH.clear()


@pytest.mark.asyncio
async def test_repository_round_trip_never_overwrites_a_newer_row(temp_database: Path) -> None:
    await init_db()
    repository = LinkHealthRepository(get_sessionmaker())
    try:
        await repository.save({"frontu": _o("frontu", 200, 10)})
        await repository.save({"frontu": _o("frontu", 503, 1), "entra-id": _o("entra-id", 200)})
        stored = await repository.load()
    finally:
        await get_engine().dispose()

    assert stored["frontu"].status_code == 200 and stored["frontu"].checked_at == T0 + timedelta(
        minutes=10
    )
    assert stored["entra-id"].status_code == 200
