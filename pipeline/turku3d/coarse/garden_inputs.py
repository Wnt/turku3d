"""Complete mapped courtyard water and lidar-supported garden terrace levels."""

import json

import numpy as np
import shapely

from turku3d import config as C


def polygon(geom):
    geom = shapely.force_2d(shapely.make_valid(geom))
    if geom.geom_type == "LineString" and geom.is_ring:
        geom = shapely.make_valid(shapely.Polygon(geom))
    return geom if geom.geom_type in ("Polygon", "MultiPolygon") else shapely.GeometryCollection()


def features(tile, path):
    from turku3d.spatial import rows

    gardens, pools, lines, buildings = [], [], [], []
    seen = set()
    for key, row in rows(path, tile.bbox.buffered(60)):
        tags = json.loads(row["tags"] or "{}")
        geom = shapely.force_2d(row["geometry_local"])
        area = polygon(geom)
        if tags.get("building") not in (None, "no") and not area.is_empty:
            buildings.append(area)
        if tags.get("leisure") == "garden" and 5 <= area.area <= 5000:
            gardens.extend(shapely.get_parts(area))
        formal = (
            tags.get("leisure") == "swimming_pool"
            or tags.get("water") in ("reflecting_pool", "basin", "pool")
            or tags.get("man_made") == "basin"
        )
        if tags.get("indoor") == "yes" or tags.get("layer", "0") not in ("0", ""):
            continue
        small_water = (
            tags.get("natural") == "water"
            and tags.get("water") not in ("river", "stream", "canal", "sea")
            and area.area <= 500
        )
        identity = str(row.get("osm_id", key))
        if (formal or small_water) and 2 <= area.area <= 5000 and identity not in seen:
            pools.extend(
                (g, ("osm:" if formal else "small_water:") + identity)
                for g in shapely.get_parts(area)
            )
            seen.add(identity)
        if (
            tags.get("highway") in ("footway", "steps", "pedestrian")
            and geom.geom_type == "LineString"
        ):
            lines.append(geom)
    return (
        list(shapely.get_parts(shapely.union_all(gardens))),
        pools,
        lines,
        shapely.union_all(buildings),
    )


def flat_level(points, region, *, tolerance=0.65):
    """Reject slopes; use the dominant ground band rather than a nearby wall crown."""
    values = points[shapely.contains_xy(region, points[:, 0], points[:, 2]), 1]
    if len(values) < 8:
        return None
    if np.diff(np.percentile(values, [20, 80]))[0] > tolerance:
        return None
    bins, counts = np.unique(np.floor(values / 0.2).astype(int), return_counts=True)
    centre = (bins[np.argmax(counts)] + 0.5) * 0.2
    band = values[np.abs(values - centre) <= 0.3]
    return float(np.median(band)) if len(band) >= 5 else None


def prepare(tile, osm, paths, water=None):
    from turku3d.ground.inputs import read_returns

    gardens, pools, lines, blocked = features(tile, osm)
    if water:
        from turku3d.spatial import frame

        from .banks import small_bodies

        context = [polygon(g) for g in frame(water).geometry_local]
        local = [polygon(g) for g in frame(water, tile.bbox.buffered(60)).geometry_local]
        for body in small_bodies(local, context):
            if body.area <= 500 and not any(body.intersects(g) for g, _ in pools):
                pools.append((body, "small_water:city"))
    # Small natural water needs explicit courtyard context before receiving masonry.
    pools = [
        (g, source)
        for g, source in pools
        if not source.startswith("small_water:")
        or (
            g.distance(shapely.union_all(gardens)) <= 12
            and g.area / g.minimum_rotated_rectangle.area >= 0.9
        )
    ]
    regions = [*gardens, *[g.buffer(2, join_style="mitre") for g, _ in pools]]
    if gardens:
        domain = shapely.union_all(gardens).buffer(15)
        network = shapely.union_all([g for g in lines if g.intersects(domain)])
        cells = shapely.get_parts(shapely.polygonize(shapely.get_parts(network)))
        regions.extend(g for g in cells if 10 <= g.area <= 2000 and g.distance(domain) == 0)
    regions = [g.difference(blocked.buffer(0.2)) for g in regions]
    regions = [
        g
        for g in regions
        if not g.is_empty
        and all(
            C.raw_dir("laz") / "maanpinta" / f"{b.name}.laz" in paths
            for b in C.laz_tiles_for_bbox(C.BBoxLocal(*g.buffer(3).bounds))
        )
    ]
    if not regions:
        return dict(pools=[], terraces=[])
    bounds = C.BBoxLocal(*shapely.union_all(regions).buffer(3).bounds)
    # Complete canonical blocks, never a tile-clipped shoreline/terrace sample.
    needed = [
        C.raw_dir("laz") / "maanpinta" / f"{b.name}.laz" for b in C.laz_tiles_for_bbox(bounds)
    ]
    if any(p not in paths for p in needed):
        return dict(pools=[], terraces=[])
    points, _ = read_returns(needed, bounds, [1, 2, 8, 9, 12])
    terraces = []
    for region in regions:
        level = flat_level(points, region.buffer(-0.4))
        if level is not None:
            terraces.append((region, level))
    measured = []
    for body, source in pools:
        if any(
            C.raw_dir("laz") / "maanpinta" / f"{b.name}.laz" not in paths
            for b in C.laz_tiles_for_bbox(C.BBoxLocal(*body.buffer(3).bounds))
        ):
            continue
        level = flat_level(points, body.buffer(1.5).difference(body), tolerance=0.8)
        if level is not None:
            measured.append((body, level, source))
            terraces.append((body.buffer(2, join_style="mitre"), level))
    # Stable source ordering, including overlapping patches at a visual tile seam.
    terraces = sorted(set(terraces), key=lambda row: (-row[0].area, row[0].wkb, row[1]))
    return dict(pools=measured, terraces=terraces)
