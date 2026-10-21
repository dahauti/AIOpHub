"""Independent link health for the cockpit: BC OData, Frontu v2 and the on-prem gateway.

Each indicator is derived only from its own service's latest HTTP outcome, so a Frontu
failure never turns BC red. Outcomes come from this process (live) merged with the
`link_health` rows persisted by CLI runs; the newer outcome per service wins.
"""

import logging
from collections.abc import Mapping
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.link_health import LINK_HEALTH, LinkOutcome
from src.repositories.link_health_repository import LinkHealthRepository

logger = logging.getLogger(__name__)

Health = Literal["ok", "failing", "unknown"]
BC_SERVICES = ("business-central", "entra-id")  # OData calls and the token they depend on
FRONTU_SERVICE = "frontu"
GATEWAY_SERVICE = "business-central"  # the on-prem OData endpoint sits behind the gateway


class LinkStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    health: Health = "unknown"
    outcome: LinkOutcome | None = None


class LinkPanel(BaseModel):
    model_config = ConfigDict(frozen=True)

    gateway: LinkStatus = LinkStatus()
    bc: LinkStatus = LinkStatus()
    frontu: LinkStatus = LinkStatus()


def _latest(outcomes: Mapping[str, LinkOutcome], *services: str) -> LinkOutcome | None:
    found = [outcomes[name] for name in services if name in outcomes]
    return max(found, key=lambda outcome: outcome.checked_at, default=None)


def _status(outcome: LinkOutcome | None, *, reachability_only: bool = False) -> LinkStatus:
    if outcome is None:
        return LinkStatus()
    healthy = outcome.reachable if reachability_only else outcome.ok
    return LinkStatus(health="ok" if healthy else "failing", outcome=outcome)


def build_panel(outcomes: Mapping[str, LinkOutcome]) -> LinkPanel:
    """Gateway = any HTTP answer from BC (network path up); BC/Frontu = their own call health."""
    return LinkPanel(
        gateway=_status(outcomes.get(GATEWAY_SERVICE), reachability_only=True),
        bc=_status(_latest(outcomes, *BC_SERVICES)),
        frontu=_status(outcomes.get(FRONTU_SERVICE)),
    )


def merge(
    stored: Mapping[str, LinkOutcome], live: Mapping[str, LinkOutcome]
) -> dict[str, LinkOutcome]:
    merged = dict(stored)
    for service, outcome in live.items():
        if service not in merged or outcome.checked_at >= merged[service].checked_at:
            merged[service] = outcome
    return merged


@runtime_checkable
class LinkPanelPort(Protocol):
    async def panel(self) -> LinkPanel: ...


class LinkHealthReader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._repository = LinkHealthRepository(sessions)

    async def panel(self) -> LinkPanel:
        return build_panel(merge(await self._repository.load(), LINK_HEALTH.snapshot()))


async def persist_link_health(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Flush this process's outcomes; never fails the run that called it."""
    try:
        await LinkHealthRepository(sessions).save(LINK_HEALTH.snapshot())
    except SQLAlchemyError:
        logger.exception("Could not persist link health")
