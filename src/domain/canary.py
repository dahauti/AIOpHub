"""Single-entity canary target: a BC Service Order No or a Frontu task GUID."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Frontu task GUIDs are canonical 8-4-4-4-12 hex UUIDs; anything else is a BC order No.
GUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
MAX_TARGET_CHARS = 64

CanaryKind = Literal["order", "task"]


class CanaryTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: CanaryKind
    value: str = Field(min_length=1, max_length=MAX_TARGET_CHARS)


def is_guid(value: str) -> bool:
    return GUID_PATTERN.fullmatch(value) is not None


def parse_canary_target(raw: str) -> CanaryTarget:
    """Classify user input; raises ValueError when blank or too long."""
    value = raw.strip()
    if not value:
        raise ValueError("Enter a BC Service Order No or a Frontu task GUID")
    if len(value) > MAX_TARGET_CHARS:
        raise ValueError(f"Canary target is longer than {MAX_TARGET_CHARS} characters")
    return CanaryTarget(kind="task" if is_guid(value) else "order", value=value)
