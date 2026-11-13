"""The kinds of the world pipeline, by name. A track replaces its kind's row with its own generator."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from ..api import Kind
from . import building, fence, furniture, ground, lamp, planting, signal, signpost, trees, wall, water, wire

KINDS: dict[str, Kind] = {kind.name: kind for kind in (ground.Ground(), water.Water(), wall.Wall(), building.Building(), trees.Trees(), planting.Planting(), signpost.SignPost(), lamp.Lamp(), fence.Fence(), wire.Wire(), furniture.Furniture(), signal.Signal())}

HERE = Path(__file__).parent
SHARED = (HERE.parent / "api.py", HERE.parent / "geometry.py", HERE.parent / "glb.py", HERE.parent / "frame.py")
_hashes: dict[str, str] = {}


def code_hash(name: str) -> str:
    """Hash of the source files a kind's output depends on: its module (the whole package when it is one), any
    extra paths it lists in `code`, and the shared mesh code. Editing a generator rebuilds its entities."""
    if name not in _hashes:
        kind = KINDS[name]
        module = Path(sys.modules[type(kind).__module__].__file__)
        paths = [module.parent if module.name == "__init__.py" else module, *getattr(kind, "code", ())]
        files = sorted({f for p in paths for f in ([p] if Path(p).is_file() else Path(p).rglob("*.py")) if "tests" not in f.parts})
        digest = hashlib.sha256()
        for path in [*files, *SHARED]:
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
        _hashes[name] = digest.hexdigest()
    return _hashes[name]
