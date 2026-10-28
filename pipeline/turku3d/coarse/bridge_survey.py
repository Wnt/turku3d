"""Optional full-resolution city lidar evidence; never fetch during draft generation."""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.lidar import TileStore


def source_paths(tile):
    """Include optional survey identities in both prepared and served draft caches."""
    result = []
    for block in C.laz_tiles_for_bbox(tile.bbox.buffered(500)):
        for directory in (C.raw_dir("laz") / "pistepilvi", C.raw_dir("laz-3x3")):
            path = directory / f"{block.name}.laz"
            if path.exists():
                result.append(path)
                break
    return result


def measure(way, points, classes):
    """Robust deck stations with held-out residuals; abstain on sparse/noisy returns.

    Road furniture is rejected by the dominant 20 cm height band. Every fifth return
    is withheld from the profile, including its interpolation and smoothing.
    """
    axis, width = way["line"], way["width"]
    s = np.linspace(0, axis.length, max(3, int(np.ceil(axis.length / 3)) + 1))
    projected = shapely.line_locate_point(axis, shapely.points(points[:, [0, 2]]))
    near = shapely.distance(axis, shapely.points(points[:, [0, 2]])) < min(2, width * 0.3)
    near &= np.isin(classes, (1, 2, 10, 12, 17))
    held = np.arange(len(points)) % 5 == 0
    h = np.full(len(s), np.nan)
    for i, station in enumerate(s):
        selection = near & ~held & (np.abs(projected - station) < 2)
        values = points[selection, 1]
        if len(values) < 12:
            continue
        bins = np.floor(values / 0.2).astype(int)
        ids, counts = np.unique(bins, return_counts=True)
        centre = (ids[np.argmax(counts)] + 0.5) * 0.2
        values = values[np.abs(values - centre) < 0.25]
        if len(values) >= 12 and np.std(values) < 0.13:
            h[i] = np.median(values)
    known = np.isfinite(h)
    report = {"profile_source": "ground-key-point fallback", "coverage": float(known.mean())}
    if known.mean() < 0.8 or not known[0] or not known[-1]:
        return report
    h = np.interp(s, s[known], h[known])
    if np.max(np.abs(np.diff(h) / np.diff(s))) > 0.25:
        return report
    residual = np.abs(points[near & held, 1] - np.interp(projected[near & held], s, h))
    validation = points[near & held]
    report["validation_points"] = validation[:: max(1, len(validation) // 2048)].tolist()
    report.update(
        profile_source="city lidar dominant deck returns",
        stations=s.tolist(),
        heights=h.tolist(),
        heldout_count=len(residual),
        median_error_m=float(np.median(residual)),
        p90_error_m=float(np.quantile(residual, 0.9)),
    )
    # Width is an independent cross-section occupancy check, not a refit to the OSM outline.
    delta = np.asarray(axis.coords[-1]) - np.asarray(axis.coords[0])
    normal = np.array([-delta[1], delta[0]]) / np.linalg.norm(delta)
    lateral = (points[:, [0, 2]] - np.asarray(axis.coords[0])) @ normal
    widths = []
    for station in (axis.length * 0.25, axis.length * 0.5, axis.length * 0.75):
        mask = (np.abs(projected - station) < 2) & (np.abs(lateral) < width / 2 + 1)
        mask &= np.isin(classes, (1, 2, 10, 12, 17))
        mask &= np.abs(points[:, 1] - np.interp(projected, s, h)) < 0.25
        bins, counts = np.unique(np.floor(lateral[mask] / 0.25).astype(int), return_counts=True)
        occupied = set(bins[counts >= 3])
        # Only the contiguous occupied cross-section through the deck centre counts.
        if 0 not in occupied:
            continue
        lo = hi = 0
        while lo - 1 in occupied:
            lo -= 1
        while hi + 1 in occupied:
            hi += 1
        if (hi - lo + 1) * 0.25 > width * 0.6:
            widths.append((hi - lo + 1) * 0.25)
    if len(widths) >= 2:
        report["lidar_width_m"] = float(np.median(widths))
        report["width_error_m"] = float(width - report["lidar_width_m"])
    report["underside_source"] = "unmeasured; type-based thickness"
    report["pier_stations"] = pier_stations(axis, width, points, classes, s, h)
    report["pier_bases"] = []
    for station in report["pier_stations"]:
        mask = (np.abs(projected - station) < 3) & (np.abs(lateral) < width / 2 + 0.5)
        mask &= np.isin(classes, (1, 10, 12, 17)) & (points[:, 1] > 1)
        mask &= np.interp(projected, s, h) - points[:, 1] > 1
        report["pier_bases"].append(float(np.quantile(points[mask, 1], 0.05) - 0.5))
    report["pier_source"] = (
        "vertical sub-deck lidar clusters with type-based completion"
        if report["pier_stations"]
        else "type-based span layout, excluding mapped underpasses"
    )
    return report


def pier_stations(axis, width, points, classes, stations, heights):
    """Vertical sub-deck return clusters, excluding banks and horizontal soffits.

    This is positive support evidence only: absent returns do not prove no pier.
    """
    geometry = shapely.points(points[:, [0, 2]])
    along = shapely.line_locate_point(axis, geometry)
    distance = shapely.distance(axis, geometry)
    top = np.interp(along, stations, heights)
    margin = max(6, axis.length * 0.12)
    selected = (distance < width / 2 + 0.5) & (along > margin) & (along < axis.length - margin)
    selected &= np.isin(classes, (1, 10, 12, 17))
    selected &= (points[:, 1] > 1) & (top - points[:, 1] > 1) & (top - points[:, 1] < 5)
    indices = np.flatnonzero(selected)
    bins, counts = np.unique(np.floor(along[indices]).astype(int), return_counts=True)
    occupied = bins[counts >= 4]
    groups = np.split(occupied, np.flatnonzero(np.diff(occupied) > 3) + 1)
    result = []
    for group in groups:
        if not len(group) or group[-1] - group[0] > 5:
            continue
        cluster = indices[(along[indices] >= group[0]) & (along[indices] < group[-1] + 1)]
        if len(cluster) < 10 or np.ptp(points[cluster, 1]) < 0.75:
            continue
        result.append(float(np.median(along[cluster])))
    return result


def survey(way, paths):
    if not paths or way.get("landing"):
        return {"profile_source": "ground-key-point fallback"}
    # New bridges cannot be measured from the older city flight.
    year = str(way["tags"].get("start_date", way["tags"].get("construction_year", "")))[:4]
    if year.isdigit() and int(year) > 2021:
        return {
            "profile_source": "ground-key-point fallback",
            "reason": "bridge postdates 2021 lidar",
        }
    bbox = C.BBoxLocal(*way["line"].buffer(way["width"] / 2 + 2).bounds)
    sources = {p.stem: p for p in paths}
    if any(b.name not in sources for b in C.laz_tiles_for_bbox(bbox)):
        return {"profile_source": "ground-key-point fallback", "reason": "full lidar unavailable"}
    cloud = TileStore(source_paths=sources).query(bbox)
    result = measure(way, cloud.points, cloud.records["classification"])
    result["sources"] = [str(sources[b.name]) for b in C.laz_tiles_for_bbox(bbox)]
    return result
