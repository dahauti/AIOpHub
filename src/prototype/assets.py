"""Prototipo statinių failų URL su turinio maiša (cache-busting).

`?v=` pasikeičia kartu su failo turiniu, todėl naršyklė niekada nesumaišo naujo HTML su sena
CSS kopija. Maiša talpinama pagal failo mtime: pakeitus CSS nereikia perkrauti serverio.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
VERSION_CHARS = 12


@lru_cache(maxsize=32)
def _digest(path: Path, mtime_ns: int) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:VERSION_CHARS]


def asset_url(name: str) -> str:
    """`/static/<name>?v=<first 12 hex of the file's SHA256>`."""
    path = STATIC_DIR / name
    return f"/static/{name}?v={_digest(path, path.stat().st_mtime_ns)}"
