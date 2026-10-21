"""Line diffing in both directions: GUID-linked lines are never re-sent or re-ingested."""

import logging

from src.domain.bc_models import BCServiceOrderLine
from src.domain.frontu_models import FrontuMaterial, FrontuWork
from src.domain.mapping import filter_unlinked_bc_lines, line_hash
from src.domain.task_builders import (
    ITEM_LINE_TYPE,
    LINE_NO_STEP,
    RESOURCE_LINE_TYPE,
    build_material,
    build_work,
    line_from_material,
    line_from_work,
)
from src.domain.task_state import LineEntityType, TaskLineSyncState
from src.services.ports import BCTasksPort, FrontuTasksPort
from src.storage.task_state_repository import TaskStateRepository

logger = logging.getLogger(__name__)

GUID_FIELD = "Frontu_Integration_Line_GUID"
_OUTBOUND_TYPES: dict[str, LineEntityType] = {
    ITEM_LINE_TYPE: "MATERIAL",
    RESOURCE_LINE_TYPE: "WORK",
}


class LineReconciler:
    """Pushes unlinked BC lines to Frontu and ingests new Frontu materials/works into BC."""

    def __init__(
        self, bc: BCTasksPort, frontu: FrontuTasksPort, states: TaskStateRepository
    ) -> None:
        self._bc = bc
        self._frontu = frontu
        self._states = states

    async def push_unlinked(
        self, order_no: str, task_guid: str, lines: list[BCServiceOrderLine]
    ) -> int:
        """Push Item/Resource lines lacking a GUID; write the Frontu GUID back to BC."""
        registered = {(s.line_no, s.entity_type): s for s in await self._states.get_lines(order_no)}
        pushed = 0
        for line in filter_unlinked_bc_lines(lines):
            entity_type = _OUTBOUND_TYPES.get(line.Type)
            if entity_type is None or not line.No:
                continue
            known = registered.get((line.LineNo, entity_type))
            guid = known.frontu_entity_guid if known is not None else None
            if guid is None:
                guid = await self._push(line, entity_type, task_guid)
                await self._save(order_no, line, guid, entity_type)
                pushed += 1
            await self._bc.update_order_line(order_no, line.LineNo, {GUID_FIELD: guid})
        return pushed

    async def ingest(self, order_no: str, task_guid: str) -> int:
        """Insert Frontu materials/works not yet linked to any BC line; unmapped -> comment."""
        bc_lines = await self._bc.fetch_order_lines(order_no)
        registered = await self._states.get_lines(order_no)
        known = {line.Frontu_Integration_Line_GUID for line in bc_lines}
        known.update(state.frontu_entity_guid for state in registered)
        used = [line.LineNo for line in bc_lines] + [state.line_no for state in registered]
        next_no = max(used, default=0) + LINE_NO_STEP
        created = 0
        entities: list[FrontuMaterial | FrontuWork] = [
            *await self._frontu.fetch_task_materials(task_guid),
            *await self._frontu.fetch_task_works(task_guid),
        ]
        for entity in entities:
            if entity.guid is None or entity.guid in known:
                if entity.guid is None:
                    logger.warning(
                        "Skipping Frontu entity without guid",
                        extra={"service_order_no": order_no, "frontu_task_guid": task_guid},
                    )
                continue
            line = _to_bc_line(order_no, next_no, entity)
            await self._bc.create_order_line(line)
            await self._save(order_no, line, entity.guid, _entity_type(entity))
            known.add(entity.guid)
            next_no += LINE_NO_STEP
            created += 1
        return created

    async def _push(
        self, line: BCServiceOrderLine, entity_type: LineEntityType, task_guid: str
    ) -> str:
        if entity_type == "MATERIAL":
            return await self._frontu.push_material(build_material(line, task_guid))
        return await self._frontu.push_work(build_work(line, task_guid))

    async def _save(
        self, order_no: str, line: BCServiceOrderLine, guid: str, entity_type: LineEntityType
    ) -> None:
        state = TaskLineSyncState(
            service_order_no=order_no,
            line_no=line.LineNo,
            frontu_entity_guid=guid,
            entity_type=entity_type,
            line_hash=line_hash(line),
            sync_status="SYNCED",
        )
        await self._states.save_line(state)


def _to_bc_line(
    order_no: str, line_no: int, entity: FrontuMaterial | FrontuWork
) -> BCServiceOrderLine:
    if isinstance(entity, FrontuMaterial):
        return line_from_material(order_no, line_no, entity)
    return line_from_work(order_no, line_no, entity)


def _entity_type(entity: FrontuMaterial | FrontuWork) -> LineEntityType:
    if not entity.systemId.strip():
        return "COMMENT"
    return "MATERIAL" if isinstance(entity, FrontuMaterial) else "WORK"
