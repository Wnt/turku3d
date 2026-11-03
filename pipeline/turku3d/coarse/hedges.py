"""Measure mapped clipped hedges; preserve parallel paths and cut actual crossings."""

import json
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.stage_cache import source_key
from turku3d.tile_layer import identities


def dimensions(line, points, classes):
    from scipy.spatial import cKDTree

    ground = points[classes == 2]
    veg = points[np.isin(classes, [3, 4, 5])]
    if len(ground) < 10 or len(veg) < 30 or line.length < 2:
        return None
    distance, nearest = cKDTree(ground[:, [0, 2]]).query(veg[:, [0, 2]])
    heights = veg[:, 1] - ground[nearest, 1]
    offset = shapely.distance(line, shapely.points(veg[:, [0, 2]]))
    keep = (distance < 4) & (offset < 2) & (heights > .45) & (heights < 4.5)
    veg, heights, offset = veg[keep], heights[keep], offset[keep]
    if len(veg) < 30:
        return None
    along = shapely.line_locate_point(line, shapely.points(veg[:, [0, 2]]))
    bins = max(1, int(np.ceil(line.length / 2.5)))
    cell = np.minimum((along / line.length * bins).astype(int), bins - 1)
    tops = [np.percentile(heights[(cell == i) & (offset < .9)], 70)
            for i in range(bins) if np.sum((cell == i) & (offset < .9)) >= 6]
    if len(tops) < bins * .6 or np.ptp(np.percentile(tops, [20, 80])) > .9:
        return None  # Scattered bushes/tree branches are not a flat clipped hedge.
    top = float(np.median(tops))
    top_returns = veg[(abs(heights - top) < .55) & (offset < .9), 1]
    if len(top_returns) < 6:
        return None
    # A hedge following a hill keeps its measured height above the terrain.
    # Only an actually level crown supports a horizontal world-space top.
    level = (round(float(np.median(top_returns)), 2)
             if np.ptp(np.percentile(top_returns, [20, 80])) <= .9 else None)
    selected = abs(heights - top) < .55
    if level is not None:
        selected &= abs(veg[:, 1] - level) < .3
    crown = veg[selected][:, [0, 2]]
    if len(crown) < 6:
        return None
    at = shapely.line_locate_point(line, shapely.points(crown))
    centre = shapely.get_coordinates(shapely.line_interpolate_point(line, at))
    before = shapely.get_coordinates(shapely.line_interpolate_point(line, np.maximum(at - .05, 0)))
    after = shapely.get_coordinates(shapely.line_interpolate_point(line, np.minimum(at + .05, line.length)))
    tangent = after - before
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-9)
    delta = crown - centre
    signed = tangent[:, 0] * delta[:, 1] - tangent[:, 1] * delta[:, 0]
    # Distance from an offset OSM line is not a radius. Fit both crown edges;
    # trim branches/outliers instead of doubling a one-sided mapping error.
    low, high = np.percentile(signed, [20, 80])
    width = float(np.clip(high - low, .45, 3))
    shift = float(np.clip((low + high) / 2, -1.5, 1.5))
    return round(top, 2), round(width, 2), level, round(shift, 2)


def crossing_mask(line, paths, bounds=None):
    """``bounds`` (shapely.bounds of the paths) skips paths whose box misses the line's:
    their intersection is empty, so the cuts and their union are unchanged."""
    cuts = []
    if bounds is not None:
        x0, z0, x1, z1 = line.bounds
        near = (bounds[:, 0] <= x1) & (bounds[:, 2] >= x0) & (bounds[:, 1] <= z1) & (bounds[:, 3] >= z0)
        paths = [paths[i] for i in np.flatnonzero(near | ~np.isfinite(bounds).all(axis=1))]
    for path, width in paths:
        # Wide path polygons often overlap a parallel hedge. Only the measured
        # centreline crossing justifies removing an existing mapped hedge.
        hit = line.intersection(path)
        if hit.is_empty or hit.length > .05:
            continue
        cuts.append(hit.buffer(width / 2 + .15))
    return shapely.union_all(cuts)


def measure(tile, records, osm, paths):
    from turku3d.lidar import TileStore
    from turku3d.spatial import rows

    network = []
    for _, row in rows(osm, tile.bbox.buffered(100)) if osm else []:
        tags = json.loads(row['tags'] or '{}')
        geom = shapely.force_2d(row['geometry_local'])
        if not tags.get('highway') or geom.geom_type not in ('LineString', 'MultiLineString'):
            continue
        if (tags.get('bridge', 'no') != 'no' or tags.get('tunnel', 'no') != 'no'
                or tags.get('layer', '0') not in ('0', '')):
            continue
        try:
            width = float(tags.get('width', 2.5 if tags['highway'] in ('footway', 'path', 'steps') else 6))
        except ValueError:
            width = 2.5
        network.append((geom, float(np.clip(width, .3, 15))))
    network_bounds = shapely.bounds([g for g, _ in network]).reshape(-1, 4)
    by_name = {p.stem: p for p in paths}
    store = TileStore(source_paths=by_name) if paths else None
    result = []
    for geometry, tags, on_road in records:
        if tags['barrier'] != 'hedge':
            result.append((geometry, tags, on_road))
            continue
        for line in shapely.get_parts(geometry):
            if line.geom_type != 'LineString':
                result.append((line, tags, on_road))
                continue
            meta = dict(tags, _hedge_openings=crossing_mask(line, network, network_bounds))
            bounds = C.BBoxLocal(*line.buffer(2.2).bounds)
            if store and all(block.name in by_name for block in C.laz_tiles_for_bbox(bounds)):
                cloud = store.query(bounds, classes=[2, 3, 4, 5])
                measured = dimensions(line, cloud.points, cloud.records['classification'])
                if measured:
                    meta['_hedge_height'], meta['_hedge_width'] = measured[:2]
                    if measured[2] is not None:
                        meta['_hedge_top'] = measured[2]
                    shifted = line.offset_curve(measured[3], join_style='mitre')
                    if not shifted.is_empty:
                        line = shifted
                    meta['_hedge_openings'] = crossing_mask(line, network, network_bounds)
            result.append((line, meta, on_road))
    return result


def prepare(cache, tile, inputs, sources):
    from turku3d.ground.inputs import laz_paths
    from turku3d.lidar import cache as lidar_cache

    records = inputs.barriers
    lines = [g for g, t, _ in records if t['barrier'] == 'hedge']
    if not lines:
        return records
    paths = laz_paths(C.BBoxLocal(*shapely.union_all(lines).buffer(2.2).bounds))
    osm = sources.osm_source(tile)
    named = {str(p): p for p in [*paths, *([osm] if osm else [])]}
    return cache.run('hedge_dimensions', source_key(Path(__file__), Path(lidar_cache.__file__)),
        [tile, records, identities(named)], lambda: measure(tile, records, osm, paths))
