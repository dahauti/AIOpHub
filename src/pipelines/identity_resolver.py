"""Frontu workspace identity: adopt unmanaged legacy objects instead of duplicating them.

SPEC section 10. Managed workspaces (non-blank `systemId`) are updated by the native
`POST /workspaces` upsert; an unmanaged object whose `serialNumber` matches the vehicle's
VIN (first) or SerialNo (fallback) is adopted; several matches fail closed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from src.schemas.bc_vehicle import BCODataVehicle
from src.schemas.frontu_models import FrontuWorkspaceSummary

IdentityKind = Literal["UPSERT", "ADOPT", "AMBIGUOUS"]
OBJECT_TYPE = "object"


@dataclass(frozen=True)
class IdentityDecision:
    kind: IdentityKind
    workspace_id: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def ambiguity_detail(self) -> str:
        return (
            f"ambiguous legacy match: Frontu workspaces {', '.join(self.candidates)} "
            "share this serial number; resolve manually"
        )


def normalize_key(value: str | None) -> str:
    return (value or "").strip().upper()


class LegacyIndex:
    """One run's view of Frontu: managed systemIds plus unmanaged objects by serialNumber."""

    def __init__(self, workspaces: Iterable[FrontuWorkspaceSummary]) -> None:
        self._managed: set[str] = set()
        self._by_serial: dict[str, list[str]] = {}
        self._claimed: dict[str, str] = {}
        for item in workspaces:
            system_id = (item.systemId or "").strip()
            if system_id:
                self._managed.add(system_id)
                continue
            key = normalize_key(item.serialNumber)
            if key and (item.type is None or item.type == OBJECT_TYPE):
                self._by_serial.setdefault(key, []).append(item.id)

    def resolve(self, vehicle: BCODataVehicle) -> IdentityDecision:
        """Decide how to write this vehicle; an adopted object is reserved for it."""
        serial = vehicle.SerialNo.strip()
        if vehicle.Blocked:
            return IdentityDecision("UPSERT")
        if serial in self._claimed:
            return IdentityDecision("ADOPT", self._claimed[serial])
        if serial in self._managed:
            return IdentityDecision("UPSERT")
        for key in (normalize_key(vehicle.VIN), normalize_key(vehicle.SerialNo)):
            matches = self._by_serial.get(key, []) if key else []
            if len(matches) > 1:
                return IdentityDecision("AMBIGUOUS", candidates=tuple(sorted(matches)))
            if matches:
                return self._claim(serial, matches[0])
        return IdentityDecision("UPSERT")

    def ambiguous_groups(self) -> dict[str, list[str]]:
        """Serial keys shared by several unmanaged objects: the matches `resolve` refuses."""
        return {key: list(ids) for key, ids in sorted(self._by_serial.items()) if len(ids) > 1}

    def _claim(self, serial: str, workspace_id: str) -> IdentityDecision:
        for ids in self._by_serial.values():
            if workspace_id in ids:
                ids.remove(workspace_id)
        self._claimed[serial] = workspace_id
        return IdentityDecision("ADOPT", workspace_id)
