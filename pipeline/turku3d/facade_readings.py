"""W2 building readings as proven facade description rows: the lookup the generator uses.

The rows (``textures/library/facade-readings``, written by ``turku3d.facade_readings_convert``)
are facade-fast description rows for buildings proven neither describes nor reviews.
``facade_assets.description_row`` consults them only after every proven source, so a proven AI
description, retained description or reviewed facade program keeps priority by construction.
The converter lives in its own module, outside the generator's import closure: a converter
change then changes the rows only, and the facade-readings migration step takes a new version.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from turku3d import config as C

SOURCE = "w2-reading"


def directory() -> Path:
    return C.REPO_ROOT / "textures/library/facade-readings"


def opaque(key) -> str:
    return hashlib.sha256(str(key).encode()).hexdigest() + ".json"


@lru_cache(maxsize=2)
def _keys(path, stamp):
    return frozenset(json.loads(Path(path).read_text())["buildings"])


def keys() -> frozenset:
    """Building keys that have a reading row (the index the migration step selects tiles by)."""
    index = directory() / "index.json"
    return _keys(str(index), index.stat().st_mtime_ns) if index.is_file() else frozenset()


def row(key):
    from turku3d.facade_assets import aliases

    for identity in aliases(key):
        path = directory() / opaque(identity)
        if path.is_file():
            return {**json.loads(path.read_text()), "building_key": str(key)}
    return None
