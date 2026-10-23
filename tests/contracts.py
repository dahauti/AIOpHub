"""Recorded Frontu response bodies from contracts/frontu/ as test fixtures."""

import json
from pathlib import Path

from pydantic import JsonValue

FRONTU_CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "frontu"


def contract(name: str) -> JsonValue:
    body: JsonValue = json.loads((FRONTU_CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))
    return body
