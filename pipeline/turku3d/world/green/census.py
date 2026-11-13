"""The measured planting of a 62.5 m cell, computed once and kept in the shared source cache.

`sources/green/census-<RECIPE>/c_<i>_<j>.json` holds what `inventory.measure` (trees) and `hedges.measure` (hedges,
shrub masses, shrubs) found for the cell. A cell outside the city's lidar survey has an empty census.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..frame import cell_box_en, cell_key
from . import hedges, inventory, lidar, osm, tufts

# Names the measuring method. Change it when a cell would be measured differently (lidar.py, inventory.py,
# hedges.py, osm.py or this file): every location then re-measures, 20 to 60 s for a pilot location and minutes a
# lidar tile on a loaded host, so an edit that measures the same must leave it alone.
RECIPE = "20261113-beds-cover"


PAVED = ("asfaltti", "betoni", "kivi", "laatta", "sora", "murske", "kiveys")  # Pintamateriaali of hard ground in both registers
BEDS = ("kasvillisuus", "istutus")  # GIS:Viheralueet Pintamateriaali: planted shrubs and perennials
FENCES = ("66636", "66630", "66632", "62534")  # kanta:Rakenne: wooden and stone fences, railings


def path_of(i: int, j: int) -> Path:
    return lidar.sources_root() / "green" / f"census-{RECIPE}" / f"{cell_key(i, j)}.json"


def cell(i: int, j: int, features) -> dict:
    """The census of one cell. `features(layer, box)` reads a city WFS layer (Sources.wfs.features)."""
    path = path_of(i, j)
    if path.exists():
        return json.loads(path.read_bytes())
    box = cell_box_en(i, j)
    wide = (box[0] - inventory.MARGIN, box[1] - inventory.MARGIN, box[2] + inventory.MARGIN, box[3] + inventory.MARGIN)
    window = lidar.Window(wide)
    if not window.surveyed:
        return {"trees": [], "absent": [], "hedges": [], "masses": [], "shrubs": [], "cell": [i, j]}
    register = inventory.register_trees(features, wide)
    mapped = osm.features(wide)
    result = inventory.measure(box, window, register, mapped["trees"], keep_all=True)
    lines = [{"line": [[round(x, 2), round(y, 2)] for x, y in geom.shape.coords], "source": "kanta:Pensasaita", "key": feature.key[:12],
              "genus": feature.attrs.get("suku", ""), "surveyed": feature.attrs.get("alkuPvm", "")[:10]}
             for feature in features("kanta:Pensasaita", wide) for geom in feature.geoms if geom.type == "LineString"]
    lines += [{"line": hedge["line"], "source": "osm", "key": str(hedge["id"])} for hedge in mapped["hedges"]]
    lines.sort(key=lambda row: (row["source"], row["key"]))
    fences = [[[round(x, 2), round(y, 2)] for x, y, *_ in geom.shape.coords] for feature in features("kanta:Rakenne", wide)
              if feature.attrs.get("kplkoodi", "") in FENCES for geom in feature.geoms if geom.type == "LineString"]
    import shapely

    greens = [(feature, shapely.make_valid(shapely.force_2d(geom.shape))) for feature in features("GIS:Viheralueet", wide)
              for geom in feature.geoms if geom.type == "Polygon"]
    streets = [(feature, shapely.make_valid(shapely.force_2d(geom.shape))) for feature in features("GIS:Katualueet", wide)
               for geom in feature.geoms if geom.type == "Polygon"]
    public = shapely.union_all([shape for _, shape in greens + streets])
    paved = shapely.union_all([shape for feature, shape in greens + streets
                               if any(word in str(feature.attrs.get("Pintamateriaali", "")).lower() for word in PAVED)])
    beds = [{"shape": part, "key": feature.key[:12], "source": "GIS:Viheralueet"} for feature, shape in greens
            if any(word in str(feature.attrs.get("Pintamateriaali", "")).lower() for word in BEDS)
            for part in getattr(shape, "geoms", [shape]) if part.geom_type == "Polygon"]
    result.update(hedges.measure(box, window, lines, result.pop("window_trees"), fences, public, beds, paved))
    # Unmown ground: the register's parts first, OpenStreetMap's where the city keeps no ground.
    massed = {mass.get("bed") for mass in result["masses"]}
    areas = [("m", bed["shape"]) for bed in beds if bed["key"] not in massed]
    areas += [(kind, shape) for feature, shape in greens
             if (kind := tufts.kind_of(str(feature.attrs.get("Viheralueosalaji", "")), str(feature.attrs.get("Kunnossapitoluokka_koodi", "")),
                                       str(feature.attrs.get("Pintamateriaali", ""))))]
    for row in mapped.get("rough", []):
        kind = "f" if any(row["tags"].get(key) in values for key, values in tufts.OSM_FOREST.items()) else "m"
        areas.append((kind, row["shape"].difference(public)))
    bare = shapely.union_all([paved.buffer(0.2), *(bed["shape"] for bed in beds if bed["key"] in massed)])
    found = tufts.cover(box, window, areas, bare)
    if found:
        result["cover"] = found
    result["cell"] = [i, j]
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    part.write_bytes(inventory.dumps(result))
    part.replace(path)
    return result
