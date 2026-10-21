"""3-node pipeline schema: BC OData -> AIOpHUB engine -> Frontu v2, from stored run state.

Node latency is the last task run's elapsed time per direction (no per-call timing exists).
"""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from src.services.cockpit_models import TaskCockpit
from src.services.link_status import LinkPanel

NodeState = Literal["ok", "running", "failing", "unknown"]
UNHEALTHY = ("FAILED", "STALE")


class PipelineNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    subtitle: str
    state: NodeState
    watermark_label: str
    watermark_at: datetime | None = None
    watermark_text: str | None = None
    latency_seconds: float | None = None


def _hostname(url: str | None) -> str:
    return (urlsplit(url).hostname or url) if url else "—"


def _engine_state(task: TaskCockpit | None) -> NodeState:
    if task is None:
        return "unknown"
    if task.running:
        return "running"
    if task.status in UNHEALTHY:
        return "failing"
    return "ok" if task.last_run is not None else "unknown"


def pipeline_nodes(task: TaskCockpit | None, links: LinkPanel) -> list[PipelineNode]:
    last = task.last_run if task is not None else None
    version = task.frontu_version if task is not None else None
    return [
        PipelineNode(
            key="bc",
            title="BC OData",
            subtitle=(task.bc_company if task else None) or "—",
            state=links.bc.health,
            watermark_label="BC task watermark",
            watermark_at=task.bc_watermark if task else None,
            latency_seconds=last.outbound_seconds if last else None,
        ),
        PipelineNode(
            key="engine",
            title="AIOpHUB Engine",
            subtitle="Status Mapping & Rules",
            state=_engine_state(task),
            watermark_label="Last run",
            watermark_at=last.finished_at if last else None,
            latency_seconds=last.elapsed_seconds if last else None,
        ),
        PipelineNode(
            key="frontu",
            title="Frontu v2",
            subtitle=_hostname(task.frontu_base_url if task else None),
            state=links.frontu.health,
            watermark_label="Frontu version",
            watermark_text=str(version) if version is not None else None,
            latency_seconds=last.inbound_seconds if last else None,
        ),
    ]
