"""Canonical city street boundaries and dense-lidar kerb measurements.

Prepared separately so mesh bevel or material edits reuse the fitted survey.
"""
import hashlib
import numpy as np
import shapely
from turku3d.ground.curbs import fit_profile
from turku3d.ground.points import Surface


def source_paths(tile):
    from turku3d import config as C
    from turku3d.ground.inputs import laz_paths

    bounds = tile.bbox.buffered(12)
    sources = {p.stem: p for p in laz_paths(bounds)}
    # Optional agent-owned survey supplements; shared raw data remains read-only.
    for block in C.laz_tiles_for_bbox(bounds):
        path = C.work_root() / "lidar-supplement" / f"{block.name}.laz"
        if path.exists():
            sources.setdefault(block.name, path)
    return sorted(sources.values())


def prepare(tile, streets, paths):
    from turku3d.ground.inputs import read_returns
    from .inputs import _frame

    if not streets or not paths:
        return []
    window = shapely.box(*tile.bbox.as_tuple())
    lines = shared_lines(list(_frame(streets, tile.bbox.buffered(12))), window.buffer(8))
    if not lines:
        return []
    points, _ = read_returns(paths, tile.bbox.buffered(12), [2, 12])
    if not len(points):
        return []
    # Only the side-plane corridors contribute evidence; walls and parked cars
    # outside them cannot bias the fit. fit_profile robustly trims residuals.
    corridor = shapely.union_all([line.buffer(1.7) for _, line in lines])
    points = points[shapely.intersects_xy(corridor, points[:, 0], points[:, 2])]
    return measure(lines, points, window)


def shared_lines(rows, window):
    """Only a shared carriageway/sidewalk edge, never a building-side outline."""
    roads = [g for g, r in rows if r.Katuosalaji == "Ajorata"]
    walks = [g for g, r in rows if r.Katuosalaji == "Jalkakäytävä"]
    result = {}
    if not roads or not walks:
        return []
    road_ids, walk_ids = shapely.STRtree(walks).query(roads, predicate="intersects")
    for i, j in zip(road_ids, walk_ids, strict=True):
        edge = shapely.line_merge(roads[i].boundary.intersection(walks[j].boundary))
        for line in shapely.get_parts(edge):
            if line.geom_type != "LineString" or line.length < 4 or not line.intersects(window):
                continue
            line = shapely.normalize(line)
            key = hashlib.sha256(line.wkb).hexdigest()[:20]
            result[key] = line
    return sorted(result.items())


def measure(lines, points, window):
    """Two-metre canonical stations; measured 3.5–30 cm rises with no bridged gaps."""
    if len(points) < 6:
        return []
    surface = Surface(points)
    result = []
    for key, line in lines:
        distances = np.arange(0, line.length + 1e-8, 2.)
        centres = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        selected = shapely.intersects_xy(window.buffer(8), centres[:, 0], centres[:, 1])
        chain = []

        def flush():
            if len(chain) < 2:
                chain.clear()
                return
            centre = np.array([p for _, p, _ in chain])
            heights = np.array([h for _, _, h in chain])
            source_stations = np.array([d for d, _, _ in chain])
            # Orient every patch so its high side is left of its centreline.
            if np.median(heights[:, 1] - heights[:, 0]) < 0:
                centre, heights = centre[::-1], heights[::-1, ::-1]
                source_stations = source_stations[::-1]
            knots = np.r_[0, np.cumsum(np.linalg.norm(np.diff(centre, axis=0), axis=1))]
            if np.all(np.diff(knots) > .1):
                result.append(dict(key=f"{key}:{chain[0][0]:g}", line=shapely.LineString(centre),
                                   knots=knots, heights=heights, source_stations=source_stations))
            chain.clear()

        for d, centre in zip(distances[selected], centres[selected], strict=True):
            tangent = (np.asarray(line.interpolate(min(line.length, d + .1)).coords[0])
                       - np.asarray(line.interpolate(max(0, d - .1)).coords[0]))
            tangent /= max(np.linalg.norm(tangent), 1e-12)
            normal = np.array([-tangent[1], tangent[0]])
            fit = fit_profile(surface, centre, normal)
            if fit is None or not .035 <= abs(np.diff(fit[1])[0]) <= .30:
                flush()
                continue
            shift, heights = fit
            if chain and (d - chain[-1][0] > 2.01
                          or np.diff(heights)[0] * np.diff(chain[-1][2])[0] <= 0
                          or np.max(abs(heights - chain[-1][2])) > .6):
                flush()
            chain.append((d, centre + shift * normal, heights))
        flush()
    return result


