"""Mapped pier footprints and supported lidar deck elevations, independent of vessels."""

import json

import numpy as np
import shapely

from turku3d import config as C
from turku3d.spatial import rows

from .garden_inputs import polygon
from .water import LEVEL


def source_paths(tile):
    from .bridge_survey import source_paths as survey_paths

    ground = [C.raw_dir("laz") / "maanpinta" / f"{b.name}.laz"
              for b in C.laz_tiles_for_bbox(tile.bbox.buffered(250))]
    return [*survey_paths(tile), *[p for p in ground if p.exists()]]


def footprints(tile, path):
    """Prefer mapped areas; buffer open centre lines by width, never close them as land."""
    candidates = {}
    for key, row in rows(path, tile.bbox.buffered(100)):
        tags = json.loads(row["tags"] or "{}")
        if tags.get("man_made") != "pier":
            continue
        identity = str(row.get("osm_id", key))
        geom = shapely.force_2d(row["geometry_local"])
        area = polygon(geom)
        if identity in candidates and not candidates[identity][0].is_empty:
            continue
        candidates[identity] = (area, geom, tags)
    areas = shapely.union_all([a for a, _, _ in candidates.values() if not a.is_empty])
    result = []
    claimed = shapely.Polygon()
    ordered = sorted(candidates.items(), key=lambda item: (
        item[1][0].is_empty, item[1][2].get("floating") != "yes", item[0]))
    for identity, (area, geom, tags) in ordered:
        if area.is_empty:
            if geom.geom_type not in ("LineString", "MultiLineString"):
                continue
            # The access line often duplicates an independently mapped pontoon area.
            geom = geom.difference(areas.buffer(0.1))
            try:
                width = float(str(tags.get("width", 1.6)).removesuffix(" m"))
            except ValueError:
                width = 1.6
            width = float(np.clip(width, 0.6, 12)) if np.isfinite(width) else 1.6
            area = geom.buffer(width / 2, cap_style="flat", join_style="mitre")
        area = area.difference(claimed)
        if not area.is_empty:
            claimed = area if claimed.is_empty else claimed.union(area)
        for i, part in enumerate(shapely.get_parts(shapely.make_valid(area))):
            if part.geom_type == "Polygon" and 0.5 <= part.area <= 10000:
                result.append((f"osm:{identity}:{i}", part, tags))
    return result


def deck_height(outline, points, floating):
    """Dominant occupied deck cells reject water, quay fringes, people and mast returns."""
    fallback = LEVEL + (0.55 if floating else 0.9)
    inner = outline.buffer(-0.15)
    use = shapely.contains_xy(inner, points[:, 0], points[:, 2])
    use &= (points[:, 1] >= LEVEL + 0.18) & (points[:, 1] <= LEVEL + (1.4 if floating else 4.5))
    p = points[use]
    if len(p) < 6:
        return fallback, "waterline-fallback"
    cells = np.floor(p[:, [0, 2]] / 0.5).astype(int)
    _, unique = np.unique(cells, axis=0, return_index=True)
    p = p[unique]
    if len(p) < 4:
        return fallback, "waterline-fallback"
    # Sliding bands do not split a flat deck at an arbitrary histogram boundary.
    heights = np.sort(p[:, 1])
    ends = np.searchsorted(heights, heights + 0.18, side="right")
    start = int(np.argmax(ends - np.arange(len(heights))))
    band = heights[start:ends[start]]
    if len(band) < 4 or len(band) / len(p) < 0.35:
        return fallback, "waterline-fallback"
    return float(np.median(band)), "lidar-deck"


def prepare(tile, path, paths):
    from turku3d.ground.inputs import read_returns

    shapes = footprints(tile, path)
    if not shapes:
        return []
    bounds = C.BBoxLocal(*shapely.union_all([g for _, g, _ in shapes]).bounds)
    clouds = []
    for raw in (True, False):
        available = {p.stem: p for p in paths if (p.parent.name != "maanpinta") == raw}
        supported = [g for _, g, _ in shapes if all(
            b.name in available for b in C.laz_tiles_for_bbox(C.BBoxLocal(*g.bounds)))]
        selected = [available[name] for name in sorted({b.name for g in supported
                    for b in C.laz_tiles_for_bbox(C.BBoxLocal(*g.bounds))})]
        points, _ = read_returns(selected, bounds, [1, 2, 8, 12, 17])
        clouds.append((available, points))
    result = []
    for identity, outline, tags in shapes:
        points = np.empty((0, 3))
        required = [b.name for b in C.laz_tiles_for_bbox(C.BBoxLocal(*outline.bounds))]
        for available, cloud in clouds:
            if all(name in available for name in required):
                points = cloud
                break
        floating = tags.get("floating") == "yes"
        height, source = deck_height(outline, points, floating)
        result.append(dict(id=identity, kind="pier", outline=outline, tags=tags,
                           water=LEVEL, deck=height, deck_source=source, floating=floating,
                           cabins=[], masts=[], building_keys=[], source_facades=[], appearance={},
                           owner=C.VisualTile.at(outline.centroid.x, outline.centroid.y).id))
    return result
