"""Pure mapping from BC vehicles to Frontu workspace upserts, plus payload fingerprinting."""

import hashlib

from src.pipelines.warranty_resolver import ResolvedWarranty
from src.schemas.bc_vehicle import BCODataShipToAddress, BCODataVehicle
from src.schemas.frontu_models import FrontuCustomFieldIds, FrontuWorkspaceUpsert


def build_workspace(
    vehicle: BCODataVehicle,
    ship_to: BCODataShipToAddress | None,
    field_ids: FrontuCustomFieldIds,
    internal_client_id: str,
    warranty: ResolvedWarranty | None = None,
) -> FrontuWorkspaceUpsert:
    """Map one BC vehicle (plus optional ship-to address and warranty) to a Frontu workspace."""
    resolved = warranty if warranty is not None else ResolvedWarranty()
    title = f"{vehicle.MakeCode or ''} {vehicle.ModelCode or ''}".strip() or vehicle.SerialNo
    site_name = ship_to.Name if ship_to is not None else None
    return FrontuWorkspaceUpsert(
        systemId=vehicle.SerialNo,
        title=title,
        client=vehicle.CustomerNo or internal_client_id,
        address=ship_to.Address if ship_to is not None else None,
        city=ship_to.City if ship_to is not None else None,
        zipCode=ship_to.Post_Code if ship_to is not None else None,
        serialNumber=vehicle.VIN,
        customValues=field_ids.build_custom_values(vehicle.RegistrationNo, site_name),
        warrantyStartDate=resolved.start_date,
        warrantyEndDate=resolved.end_date,
        extendedWarrantyEndDate=resolved.extended_end_date,
        warrantyIndicatorLimit=resolved.indicator_limit,
        deleted=vehicle.Blocked,
    )


def payload_hash(payload: FrontuWorkspaceUpsert) -> str:
    """SHA-256 of the workspace JSON excluding `deleted`, so blocking alone never changes it."""
    return hashlib.sha256(payload.model_dump_json(exclude={"deleted"}).encode()).hexdigest()
