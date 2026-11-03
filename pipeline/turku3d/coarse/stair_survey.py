"""Optional cached lidar profiles for mapped stair flights and their landings."""

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


def _level(heights):
    """Dominant 0.1 m band; a person or a wall face never sets a landing level."""
    if len(heights) < 4:
        return np.nan
    bins, counts = np.unique(np.floor(heights / 0.1).astype(int), return_counts=True)
    centre = (bins[np.argmax(counts)] + 0.5) * 0.1
    band = heights[np.abs(heights - centre) < 0.15]
    return float(np.median(band)) if len(band) >= 3 else np.nan


def flight(line, points, sides, step=0.5, reach=15.0):
    """Measured axis and width of a wide straight flight mapped without a width.

    A wide entrance flight is often mapped as one short centre way. Lidar ground
    shows where the same bottom and top landings continue across it; the flight
    ends where they stop (a wall, a cheek wall, a building). Its risers also give
    the true ascent direction when the mapped way is skewed. Both measured sides
    must meet mapped side structures, so an embankment or a quay band beside an
    ordinary stair never widens it.
    """
    coords = np.asarray(line.coords)[:, :2]
    if len(coords) != 2 or line.length < 0.8 or sides is None or sides.is_empty:
        return None
    a, b = coords
    u = (b - a) / line.length
    middle = (a + b) / 2
    xz, h = points[:, [0, 2]], points[:, 1]
    rel = xz - middle
    core = (np.abs(rel @ np.array([-u[1], u[0]])) < 1.0) & (np.abs(rel @ u) < line.length / 2 - 0.15)
    if core.sum() < 12:
        return None
    fit = np.linalg.lstsq(np.c_[np.ones(core.sum()), xz[core]], h[core], rcond=None)[0]
    gradient = fit[1:]
    if np.linalg.norm(gradient) < 0.2:
        return None
    ascent = gradient / np.linalg.norm(gradient) * np.sign(gradient @ u)
    if ascent @ u < np.cos(np.radians(35)):
        return None
    u = ascent  # Keep the mapped orientation and length, along the measured risers.
    n = np.array([-u[1], u[0]])
    a, half = middle - u * line.length / 2, line.length / 2
    s, t = (xz - a) @ u, (xz - a) @ n

    def ends(offset):
        band = np.abs(t - offset) < 0.3
        return (_level(h[band & (s > -1.0) & (s < -0.15)]),
                _level(h[band & (s > line.length + 0.15) & (s < line.length + 1.0)]))

    lo0, hi0 = ends(0.0)
    if not (np.isfinite(lo0) and np.isfinite(hi0)) or abs(hi0 - lo0) < 0.3:
        return None
    extents = []
    for sign in (1, -1):
        last, misses, k = 0.0, 0, 1
        while k * step <= reach:
            lo, hi = ends(sign * k * step)
            if (np.isfinite(lo) and np.isfinite(hi) and abs(lo - lo0) <= 0.25
                    and abs(hi - hi0) <= 0.25 and (hi - lo) / (hi0 - lo0) >= 0.6):
                last, misses = k * step, 0
            else:
                misses += 1
                if misses >= 2:
                    break
            k += 1
        if k * step > reach:
            return None  # Unbounded: a band along a quay or an embankment.
        extents.append(last + step / 2)
    width = sum(extents)
    centre = middle + n * (extents[0] - extents[1]) / 2
    if width < 3.0:
        return None
    for sign, extent in ((1, extents[0]), (-1, extents[1])):
        edge = shapely.Point(middle + n * sign * extent)
        if edge.distance(sides) > 1.5:
            return None
    return shapely.LineString([centre - u * half, centre + u * half]), float(width)


def attach(rows, paths, bridges, footways, sides=None, areas=()):
    """Survey complete mapped flights, including ordinary quay and embankment stairs."""
    from turku3d import config as C
    from turku3d.lidar import TileStore

    if not paths:
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
    for index, (key, line, tags) in enumerate(rows):
        if tags.get("bridge", "no") != "no" and line.boundary.distance(access) > 0.05:
            continue  # A standalone suspended stair needs its own deck evidence.
        bbox = C.BBoxLocal(*line.buffer(2).bounds)
        if any(block.name not in sources for block in C.laz_tiles_for_bbox(bbox)):
            continue
        cloud = store.query(bbox)
        try:
            width = float(tags.get("width", 2.5))
        except ValueError:
            width = 2.5
        reach = C.BBoxLocal(*line.buffer(16).bounds)
        if (
            "width" not in tags
            and sides is not None
            and not sides.is_empty
            and sides.distance(line) < 16
            and all(block.name in sources for block in C.laz_tiles_for_bbox(reach))
            and not any(area.covers(line.interpolate(0.5, normalized=True)) for area in areas)
        ):
            measured = flight(line, store.query(reach).query(classes=[2]).points, sides)
            if measured is not None:
                line, width = measured
                rows[index] = (key, line, tags)
                tags["_flight_width"] = round(width, 2)
                cloud = store.query(C.BBoxLocal(*line.buffer(2).bounds))
        profile = measure(line, width, cloud.points, cloud.records["classification"])
        if profile:
            tags["_profile"] = profile
