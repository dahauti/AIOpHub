"""Offline identity matching between BC vehicles and Frontu legacy workspaces (SPEC 10).

Used only by the read-only operations reports in scripts/; the sync pipelines always upsert
by `systemId`. A workspace with a non-blank `systemId` is managed. An unmanaged object whose
`serialNumber` matches a vehicle's VIN (first) or SerialNo (fallback) is a LEGACY_MATCH that
operations must link by setting `systemId` in Frontu; several matches are AMBIGUOUS.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from src.schemas.bc_vehicle import BCODataVehicle
from src.schemas.frontu_models import FrontuWorkspaceSummary

IdentityKind = Literal["UPSERT", "LEGACY_MATCH", "AMBIGUOUS"]
MatchKey = Literal["VIN", "SerialNo"]
OBJECT_TYPE = "object"


@dataclass(frozen=True)
class IdentityDecision:
    kind: IdentityKind
    workspace_id: str | None = None
    candidates: tuple[str, ...] = ()
    matched_on: MatchKey | None = None


def normalize_key(value: str | None) -> str:
    return (value or "").strip().upper()


class LegacyIndex:
    """One run's view of Frontu: managed systemIds plus unmanaged objects by serialNumber."""

    def __init__(self, workspaces: Iterable[FrontuWorkspaceSummary]) -> None:
        self._managed: set[str] = set()
        self._by_serial: dict[str, list[str]] = {}
        self._claimed: dict[str, IdentityDecision] = {}
        for item in workspaces:
            system_id = (item.systemId or "").strip()
            if system_id:
                self._managed.add(system_id)
                continue
            key = normalize_key(item.serialNumber)
            if key and (item.type is None or item.type == OBJECT_TYPE):
                self._by_serial.setdefault(key, []).append(item.id)

    def resolve(self, vehicle: BCODataVehicle) -> IdentityDecision:
        """Decide how to write this vehicle; a matched legacy object is reserved for it."""
        serial = vehicle.SerialNo.strip()
        if vehicle.Blocked:
            return IdentityDecision("UPSERT")
        if serial in self._claimed:
            return self._claimed[serial]
        if serial in self._managed:
            return IdentityDecision("UPSERT")
        keys: tuple[tuple[MatchKey, str], ...] = (
            ("VIN", normalize_key(vehicle.VIN)),
            ("SerialNo", normalize_key(vehicle.SerialNo)),
        )
        for matched_on, key in keys:
            matches = self._by_serial.get(key, []) if key else []
            if len(matches) > 1:
                return IdentityDecision("AMBIGUOUS", candidates=tuple(sorted(matches)))
            if matches:
                return self._claim(
                    serial, IdentityDecision("LEGACY_MATCH", matches[0], (), matched_on)
                )
        return IdentityDecision("UPSERT")

    def ambiguous_groups(self) -> dict[str, list[str]]:
        """Serial keys shared by several unmanaged objects: the matches `resolve` refuses."""
        return {key: list(ids) for key, ids in sorted(self._by_serial.items()) if len(ids) > 1}

    def _claim(self, serial: str, decision: IdentityDecision) -> IdentityDecision:
        for ids in self._by_serial.values():
            if decision.workspace_id in ids:
                ids.remove(decision.workspace_id)
        self._claimed[serial] = decision
        return decision
