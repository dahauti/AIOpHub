"""Resolved warranty DTO shared by the resolver, the mapping and the debug inspector."""

from pydantic import BaseModel, ConfigDict


class ResolvedWarranty(BaseModel):
    """Warranty facts for one vehicle, dates formatted as `YYYY-MM-DD`."""

    model_config = ConfigDict(frozen=True)

    start_date: str | None = None
    end_date: str | None = None
    extended_end_date: str | None = None
    indicator_limit: int | None = None
