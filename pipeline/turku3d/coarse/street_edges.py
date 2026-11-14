"""Street edges outside the centre: cast concrete kerb stones and grass verges along the streets.

Scope (Jonni, 2026-11-13: every rule carries an area scope). The city's milieu typing of its centre
(``coarse.milieus``: 33 zones, about 8 km², every street of the old grid inside them) is the one data
signal of where the streets were laid with granite. Of the carriageway kerbs read in W2's street photos
(76 locations, 68 kerbs read granite or concrete; pp-kerbs 2026-11-14), those inside a zone read
granite 28 times and concrete 3 (town types 17:1, the other types 11:2: the post-war, industrial and
changing-city zones are built on the same granite street grid), those outside every zone concrete 29
and granite 8. Outside the map the area type (W2's floor area by use within 150 m) does not split the
rule: houses read concrete 14:2, work 10:0, apartments 5:6 (a tie, the 6 granite mostly the inner
ring's old streets); the centre and rural types have no read kerb outside the map. So the scope is
the milieu map, all types, grown by GRANITE_REACH so that the streets on its edge keep one stone on
both sides: kerbs inside keep the proven granite, kerbs outside are concrete. Without the milieu
table (another city, a source version without the overlay) nothing changes.

Verges. Where neither the street register nor the open map says what lies beside a street and the
orthophoto saw no lawn (shade, dry grass, a parked car), the proven ground is the unmapped base, the
ortho's neutral cover drawn as asphalt, up to the carriageway. W2's street photos read grass verges
there (of 139 verges read outside the centre's milieu map, 126 grass; w2-rules 2026-11-13 found 30
on that base). So a strip of VERGE_M beyond a street's carriageway is grass where it is still the
base, outside the same milieu map: every mapped surface, lawn, path, driveway, finish and the ortho's
own grass and gravel outlines stay on top. Motorways, trunk roads and their links keep their asphalt
shoulders; the carriageway edge is the drawn corridor or, wider, the class's read width or the lanes.
"""

from __future__ import annotations

import json

import numpy as np
import shapely

from turku3d.col.materials import mat_id

from .street_materials import CONCRETE_CURB

CLOSE_M = 10.0  # m: the slivers between neighbouring zones are part of the map
GRANITE_REACH = 20.0  # m beyond the zones: a street on the map's edge keeps one stone on both sides
VERGE_M = 3.0
# Streets whose verges the rule draws, with the half width of their read carriageway (W2's gap street views,
# w2-rules 2026-11-13); the proven corridor (coarse.surfaces.osm_surfaces, 6 m or the width tag) when wider.
VERGE_HALF = {"primary": 4.0, "secondary": 3.75, "tertiary": 3.25, "unclassified": 2.25, "residential": 2.25,
              "living_street": 2.25}
LANE_M = 3.25


def granite_area(zones) -> shapely.Geometry | None:
    """The milieu map: every zone, slivers closed, grown by GRANITE_REACH; None without the table."""
    if not zones:
        return None
    union = shapely.union_all([shapely.make_valid(shape) for shape, _ in zones])
    return union.buffer(CLOSE_M).buffer(GRANITE_REACH - CLOSE_M)


def concrete_area(tile, zones) -> shapely.Geometry | None:
    """The part of the tile outside the milieu map, or None where the rule does not apply."""
    granite = granite_area(zones)
    if granite is None:
        return None
    window = shapely.box(*tile.bbox.buffered(1).as_tuple())
    area = window.difference(granite)
    return None if area.is_empty else area


def kerb_stone(positions, indices, materials, concrete):
    """Visual surface ids with the curb triangles outside the milieu map as cast concrete."""
    curb = materials == mat_id("curb")
    if concrete is None or not curb.any():
        return materials
    centres = positions[indices[curb]].mean(axis=1)[:, [0, 2]]
    outside = shapely.contains_xy(concrete, centres[:, 0], centres[:, 1])
    if not outside.any():
        return materials
    result = materials.copy()
    result[np.flatnonzero(curb)[outside]] = CONCRETE_CURB
    return result


def _half_width(tags: dict) -> float:
    highway = tags.get("highway")
    try:
        drawn = float(str(tags.get("width", 6)).removesuffix(" m"))
    except ValueError:
        drawn = 6.0
    if not np.isfinite(drawn) or not 0.3 <= drawn <= 30:
        drawn = 6.0
    try:
        lanes = int(str(tags.get("lanes", "0")).split(";")[0])
    except ValueError:
        lanes = 0
    return max(drawn / 2, VERGE_HALF.get(highway, 3.0), lanes * LANE_M / 2 + 0.5)


def street_lines(tile, path) -> list[tuple]:
    """(line, half width) of the ground-level streets of the verge classes reaching the tile."""
    from turku3d.spatial import rows

    window = shapely.box(*tile.bbox.buffered(VERGE_M + 20).as_tuple())
    found = []
    for _, row in rows(path, tile.bbox.buffered(50)):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if tags.get("highway") not in VERGE_HALF:
            continue
        if (tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no"
                or tags.get("layer", "0") not in ("0", "") or tags.get("area") == "yes"):
            continue
        geom = shapely.force_2d(shapely.make_valid(row["geometry_local"]))
        if geom.geom_type not in ("LineString", "MultiLineString") or not geom.intersects(window):
            continue
        found.append((geom, _half_width(tags)))
    return found


def verges(tile, lines, base, concrete) -> list[tuple]:
    """``[(polygon, grass)]``: the strip of VERGE_M beyond each street's carriageway that is still the unmapped
    base (``base``: the ortho cover's neutral class) outside the milieu map; [] where the rule does not apply."""
    if concrete is None or not lines or base is None or base.is_empty:
        return []
    carriageways = shapely.union_all([line.buffer(half, cap_style="flat") for line, half in lines])
    strips = shapely.union_all([line.buffer(half + VERGE_M, cap_style="flat") for line, half in lines])
    area = strips.difference(carriageways).intersection(base).intersection(concrete)
    area = shapely.union_all([p for p in shapely.get_parts(area) if p.geom_type == "Polygon" and p.area >= 1.0])
    return [] if area.is_empty else [(area, mat_id("grass"))]


def split_kerbs(meshes, concrete):
    """The mapped kerb strips (coarse.kerbs) as (mesh, concrete) parts: faces whose centre lies outside the
    milieu map are concrete."""
    from dataclasses import replace

    if concrete is None:
        return [(m, False) for m in meshes]
    parts = []
    for m in meshes:
        centres = m.positions[m.indices].mean(axis=1)[:, [0, 2]]
        outside = shapely.contains_xy(concrete, centres[:, 0], centres[:, 1])
        if outside.all() or not outside.any():
            parts.append((m, bool(outside.all())))
            continue
        for flag in (False, True):
            used, inverse = np.unique(m.indices[outside == flag], return_inverse=True)
            parts.append((replace(m, positions=m.positions[used], uvs=m.uvs[used],
                                  indices=inverse.reshape(-1, 3).astype(np.uint32)), flag))
    return parts
