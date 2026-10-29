"""Private empirical model loading and deterministic fallback application."""

import hashlib
import json
from functools import lru_cache

from turku3d import config as C
from turku3d.facade_assets import root


@lru_cache(maxsize=2)
def _read(path, stamp):
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).digest()


def model():
    for path in (C.work_root() / "facade-rules.json", root() / "facade-rules.json"):
        if path.is_file():
            return _read(path, (path.stat().st_mtime_ns, path.stat().st_size))
    return {"donors": [], "observed_ids": []}, b"no-learned-rules"


def apply(program):
    from turkutex.facade_learning import apply as apply_distribution

    return apply_distribution(program, model()[0])


def digest(key):
    data, checksum = model()
    return b"observed-building" if str(key) in data.get("observed_ids", []) else checksum
