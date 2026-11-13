"""The measured planting of a 62.5 m cell, computed once and kept in the shared source cache.

`sources/green/census-<recipe>/c_<i>_<j>.json` holds what `inventory.measure` (trees) and `hedges.measure` (hedges,
shrub masses, shrubs) found for the cell. The
recipe is the hash of this package's measuring code, so a change of method remeasures.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..frame import cell_box_en, cell_key
from . import hedges, inventory, lidar, osm

CODE = ("lidar.py", "inventory.py", "hedges.py", "osm.py", "census.py")


def recipe() -> str:
    digest = hashlib.sha256()
    for name in CODE:
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()[:12]


def path_of(i: int, j: int) -> Path:
    return lidar.sources_root() / "green" / f"census-{recipe()}" / f"{cell_key(i, j)}.json"


def cell(i: int, j: int, features) -> dict:
    """The census of one cell. `features(layer, box)` reads a city WFS layer (Sources.wfs.features)."""
    path = path_of(i, j)
    if path.exists():
        return json.loads(path.read_bytes())
    box = cell_box_en(i, j)
    wide = (box[0] - inventory.MARGIN, box[1] - inventory.MARGIN, box[2] + inventory.MARGIN, box[3] + inventory.MARGIN)
    window = lidar.Window(wide)
    register = inventory.register_trees(features, wide)
    mapped = osm.features(wide)
    result = inventory.measure(box, window, register, mapped["trees"], keep_all=True)
    lines = [{"line": [[round(x, 2), round(y, 2)] for x, y in geom.shape.coords], "source": "kanta:Pensasaita", "key": feature.key[:12],
              "genus": feature.attrs.get("suku", ""), "surveyed": feature.attrs.get("alkuPvm", "")[:10]}
             for feature in features("kanta:Pensasaita", wide) for geom in feature.geoms if geom.type == "LineString"]
    lines += [{"line": hedge["line"], "source": "osm", "key": str(hedge["id"])} for hedge in mapped["hedges"]]
    lines.sort(key=lambda row: (row["source"], row["key"]))
    result.update(hedges.measure(box, window, lines, result.pop("window_trees")))
    result["cell"] = [i, j]
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    part.write_bytes(inventory.dumps(result))
    part.replace(path)
    return result
