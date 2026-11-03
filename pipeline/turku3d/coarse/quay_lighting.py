"""Surveyed lower-quay lighting: mapped footways, measured crests, shared night schedule."""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
from shapely.ops import substring

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh, vertex_normals
from turku3d.spatial import frame, rows

OBSERVATIONS = Path(__file__).with_name("quay_lighting_observations.json")


def source_paths(osm):
    # The complete city extract supplies named river/bridge context; local ways
    # still come from the tile's selected OSM source. Both enter input freshness.
    return [osm, *sorted((C.raw_dir("osm") / "work-3x3").glob("*.parquet")), OBSERVATIONS] if osm else []


def extent(records, policy):
    rivers, bridges = [], {name: [] for name in policy["between_bridges"]}
    for tags, geom in records:
        if geom.geom_type != "LineString":
            continue
        if tags.get("waterway") == "river" and tags.get("name") == policy["waterway"]:
            rivers.append(geom)
        if tags.get("bridge", "no") != "no" and tags.get("name") in bridges:
            bridges[tags["name"]].append(geom)
    if not rivers or any(not parts for parts in bridges.values()):
        return shapely.Polygon()
    anchors = [shapely.union_all(parts).centroid for parts in bridges.values()]
    river = shapely.line_merge(shapely.union_all(rivers))
    candidates = [line for line in shapely.get_parts(river)
                  if all(line.distance(p) < 50 for p in anchors)]
    if not candidates:
        return shapely.Polygon()
    line = max(candidates, key=lambda g: g.length)
    start, end = sorted(line.project(p) for p in anchors)
    reach = max(g.length for parts in bridges.values() for g in parts) / 2 + 15
    return substring(line, start, end).buffer(reach, cap_style=2)


@lru_cache(maxsize=4)
def _area(identities):
    """The lit riverside reach of the complete city extract: one polygon for every tile.

    ``identities`` are (path, size, mtime) of the OSM sources and then the policy, so a
    persistent worker scans the city's rows once instead of once per tile.
    """
    paths = [Path(path) for path, _, _ in identities]
    policy = json.loads(paths[-1].read_text())
    context = {}
    for path in dict.fromkeys(paths[:-1]):
        for row in frame(path).to_dict("records"):
            tags = json.loads(row.get("tags") or "{}")
            if tags.get("waterway") == "river" or tags.get("bridge", "no") != "no":
                context[str(row.get("osm_id", row.get("feature_key")))] = (tags, row["geometry_local"])
    return extent(context.values(), policy), policy


def prepare(tile, paths):
    if not paths:
        return []
    area, policy = _area(tuple((str(p), (s := Path(p).stat()).st_size, s.st_mtime_ns) for p in paths))
    if area.is_empty:
        return []
    result = []
    for _, row in rows(paths[0], tile.bbox.buffered(12)):
        tags = json.loads(row.get("tags") or "{}")
        geom = row["geometry_local"]
        pedestrian = tags.get("highway") in ("footway", "path", "pedestrian") or (
            tags.get("highway") == "cycleway" and tags.get("foot") in ("yes", "designated", "permissive"))
        if (not pedestrian
                or (tags.get("lit") == "no" and policy["footway_lit_no_excludes"])
                or tags.get("bridge", "no") != "no"
                or tags.get("tunnel", "no") not in ("no", "building_passage")
                or tags.get("surface") in ("grass", "ground", "dirt", "wood", "metal")
                or geom.geom_type != "LineString"):
            continue
        try:
            width = float(str(tags.get("width", 3)).removesuffix(" m"))
        except ValueError:
            width = 3
        width = float(np.clip(width, 1, 12)) if np.isfinite(width) else 3
        corridor = geom.buffer(width / 2 + policy["walk_margin_m"]).intersection(area)
        if not corridor.is_empty:
            result.append(corridor)
    return result


def visuals(tile, bank, walks, corridors, water_level):
    """Replace eligible wall faces with the same stone plus a baked night irradiance.

    No new lights or collision. ``night_irradiance`` (the shared _NIGHT_IRRADIANCE
    attribute, as on landmark walls) carries the downward wash; COLOR_0 stays
    unused so far-scenery pigment and daytime albedo are unchanged. The bar is
    a thin emissive diffuser in the shared ``night_`` material family.
    """
    from . import surfaces

    allowed = shapely.union_all(corridors)
    policy = json.loads(OBSERVATIONS.read_text())
    measured = shapely.union_all([p["line"].buffer(p["width"] / 2 + policy["walk_margin_m"]) for p in walks])
    eligible = allowed.intersection(measured)
    keep = np.ones(len(bank.indices), bool)
    wall_p, wall_i, masks, bar_p, bar_i = [], [], [], [], []
    depth = policy["wash_depth_m"]
    tint = np.asarray(policy["linear_rgb"], float)
    for i in range(0, len(bank.indices) - 1, 2):
        if not np.all(bank.material[i:i + 2] == mat_id("quay")):
            continue
        ix = bank.indices[i:i + 2]
        ids = np.unique(ix)
        if len(ids) != 4:
            continue
        quad = bank.positions[ids]
        top = quad[np.argsort(quad[:, 1])[-2:]]
        bottom_vertices = quad[np.argsort(quad[:, 1])[:2]]
        if np.max(np.min(np.linalg.norm(bottom_vertices[:, None, [0, 2]] - top[None, :, [0, 2]], axis=-1), axis=1)) > 1e-5:
            continue  # Clipped openings/irregular triangles retain their exact geometry.
        a, b = top
        normal = np.cross(*(bank.positions[ix[0]][1:] - bank.positions[ix[0]][0]))
        normal /= max(np.linalg.norm(normal), 1e-9)
        if abs(normal[1]) > 0.05 or np.linalg.norm(a[[0, 2]] - b[[0, 2]]) < 0.05:
            continue
        line = shapely.LineString(top[:, [0, 2]])
        if (min(top[:, 1]) < water_level + 0.4 or max(top[:, 1]) > water_level + 3.0
                or not eligible.covers(line)):
            continue
        # Preserve the complete original wall down to its actual submerged base.
        bottom = float(quad[:, 1].min())
        levels = [0, 0.08, 0.25, 0.5, depth]
        levels = [d for d in levels if d < min(top[:, 1]) - bottom]
        rows3 = [top - [0, d, 0] for d in levels]
        floor = top.copy(); floor[:, 1] = bottom
        rows3.append(floor)
        base = len(wall_p)
        reverse = np.dot(np.cross(b - a, [0, -1, 0]), normal) < 0
        for k, row3 in enumerate(rows3):
            wall_p.extend(row3)
            d = levels[k] if k < len(levels) else depth
            masks.extend([tint * max(0, 1 - d / depth) ** 2] * 2)
        for k in range(len(rows3) - 1):
            j = base + 2 * k
            faces = np.array([[j, j + 1, j + 2], [j + 1, j + 3, j + 2]])
            if reverse:
                faces = faces[:, ::-1]
            wall_i.extend(faces)
        j = len(bar_p)
        high = top + normal * 0.025 - [0, 0.035, 0]
        bar_p.extend([high[0], high[1], high[0] - [0, 0.045, 0], high[1] - [0, 0.045, 0]])
        bar_faces = np.array([[j, j + 1, j + 2], [j + 1, j + 3, j + 2]])
        bar_i.extend(bar_faces[:, ::-1] if reverse else bar_faces)  # faces the water like the wall
        keep[i:i + 2] = False
    meshes = surfaces.visuals(tile, bank.positions, bank.indices, bank.material, keep)
    if wall_p:
        positions, indices = np.asarray(wall_p), np.asarray(wall_i, np.uint32)
        normals = vertex_normals(positions, indices)
        uv = np.c_[np.where(np.abs(normals[:, 0]) > np.abs(normals[:, 2]), positions[:, 2], positions[:, 0]), positions[:, 1]]
        meshes.append(GlbMesh("quay-lit-stone", positions - tile.origin, indices, normals=normals, uvs=uv,
            night_irradiance=np.asarray(masks), color=(1, 1, 1, 1),
            extras=dict(layer="ground", material_key="draft_quay_stone_lit", object_id=f"quay-lit:{tile.id}")))
        meshes.append(GlbMesh("quay-light-bars", np.asarray(bar_p) - tile.origin, np.asarray(bar_i, np.uint32),
            color=(0.7, 0.68, 0.61, 1), emissive=tuple(float(v) for v in tint),
            extras=dict(layer="structures", material_key="night_quay_bar", object_id=f"quay-bars:{tile.id}")))
    return meshes
