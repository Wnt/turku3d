"""Optional cached lidar profiles for mapped bridge-access stairs."""

import numpy as np
import shapely
from scipy.ndimage import median_filter
from scipy.optimize import isotonic_regression


def measure(line, width, points, classes):
    """Resolve treads and level landings without fitting vegetation or adjacent walls."""
    stations = np.unique(np.r_[np.arange(0, line.length, 0.5), line.length])
    centres = shapely.get_coordinates(shapely.line_interpolate_point(line, stations))
    valid = np.isin(classes, (1, 2, 10, 12, 17))
    points = points[valid]
    heights = np.full(len(stations), np.nan)
    for i, p in enumerate(centres):
        near = np.linalg.norm(points[:, [0, 2]] - p, axis=1) < min(0.65, width * 0.3)
        values = points[near, 1]
        if len(values) < 5:
            continue
        bins, counts = np.unique(np.floor(values / 0.2).astype(int), return_counts=True)
        centre = (bins[np.argmax(counts)] + 0.5) * 0.2
        band = values[np.abs(values - centre) < 0.25]
        if len(band) >= 5 and np.std(band) < 0.18:
            heights[i] = np.median(band)
    known = np.isfinite(heights)
    if known.mean() < 0.75 or not known[0] or not known[-1] or np.max(np.diff(stations[known])) > 4:
        return None
    heights = np.interp(stations, stations[known], heights[known])
    heights = isotonic_regression(
        median_filter(heights, size=3, mode="nearest"), increasing=heights[-1] >= heights[0]
    ).x
    # A flight's measured flat sections are landings, not shallow stair ramps.
    flat = np.abs(np.diff(heights)) < 0.045
    edges = np.flatnonzero(np.diff(np.r_[False, flat, False]))
    for start, end in edges.reshape(-1, 2):
        if stations[end] - stations[start] >= 1:
            heights[start : end + 1] = np.median(heights[start : end + 1])
    profile = np.asarray(shapely.LineString(np.c_[stations, heights]).simplify(0.06).coords)
    return dict(
        stations=profile[:, 0].tolist(),
        heights=profile[:, 1].tolist(),
        source="city lidar stair treads and landings",
        coverage=float(known.mean()),
    )


def attach(rows, paths, bridges, footways):
    """Survey only bridge-connected flights; ordinary stairs keep the cheap terrain path."""
    from turku3d import config as C
    from turku3d.lidar import TileStore

    if not paths or not bridges:
        return
    ends = shapely.union_all([line.boundary for line in bridges])
    access = shapely.union_all(
        [
            ends,
            *[
                line
                for line in footways
                if line.length <= 10 and line.boundary.distance(ends) < 0.05
            ],
        ]
    )
    sources = {p.stem: p for p in paths}
    store = TileStore(source_paths=sources)
    for _, line, tags in rows:
        if line.boundary.distance(access) > 0.05:
            continue
        bbox = C.BBoxLocal(*line.buffer(2).bounds)
        if any(block.name not in sources for block in C.laz_tiles_for_bbox(bbox)):
            continue
        cloud = store.query(bbox)
        try:
            width = float(tags.get("width", 2.5))
        except ValueError:
            width = 2.5
        profile = measure(line, width, cloud.points, cloud.records["classification"])
        if profile:
            tags["_profile"] = profile
