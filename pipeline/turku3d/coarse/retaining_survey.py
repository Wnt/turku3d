"""Two-sided lidar planes on surveyed retaining-wall tops, before terrain smoothing."""

import numpy as np
import shapely
from scipy.ndimage import median_filter
from scipy.spatial import cKDTree

from turku3d import config as C
from turku3d.lidar import Surface, TileStore, measure_profile
from turku3d.spatial import frame

from .retaining import sections


def ground_points(cloud):
    """Class 12 contains overlapping canopy scans too; admit only near-ground returns.

    Keep all surveyed class-2 breakline support. Nearest ground evidence avoids
    averaging the two sides of a vertical step before it has been measured.
    """
    classes = cloud.records["classification"]
    reference = cloud.points[classes == 2]
    extra = cloud.points[classes == 12]
    if not len(reference) or not len(extra):
        return reference
    distance, owner = cKDTree(reference[:, [0, 2]]).query(extra[:, [0, 2]])
    keep = (distance < 1.0) & (abs(extra[:, 1]-reference[owner, 1]) < 0.2+0.5*distance)
    return np.concatenate([reference, extra[keep]])


def measure(line, points):
    """Keep sustained steps; class-filtered, trimmed planes reject isolated people/objects."""
    section = sections(line)
    if section is None or len(points) < 12:
        return []
    centres, left, right = section
    surface = Surface(points)
    fits = []
    for centre, a, b in zip(centres, left, right, strict=True):
        normal = (b - a) / np.linalg.norm(b - a)
        candidates = []
        # A wall top is not a curb: the strongest raw gradient can be its face,
        # a seated person, or the grass above the cap. Compare supported planes
        # around the survey instead of snapping to that single gradient peak.
        for shift in (-0.3, 0.0, 0.3):
            fit = measure_profile(surface, centre + shift * normal, normal, snap=False,
                                  along_limit=2.0, side_min=0.2, side_max=1.0)
            if (fit is not None and fit.confidence >= 0.2
                    and 0.3 <= abs(fit.step) <= 6.0):
                fit.shift = shift
                candidates.append(fit)
        fits.append(max(candidates, key=lambda f: f.confidence * abs(f.step))
                    if candidates else None)
    runs, run = [], []

    def emit():
        if len(run) < 2:
            return
        chosen = [fits[i] for i in run]
        normals = right[run] - left[run]
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        shifted = centres[run] + np.array([f.shift for f in chosen])[:, None] * normals
        levels = median_filter(np.array([f.heights for f in chosen]), size=(3, 1), mode="nearest")
        # Use the same mitered sections in the emitter, including mapped bends.
        offsets = (right[run] - left[run]) / 2
        runs.append((shapely.LineString(shifted), dict(
            centres=shifted, left=shifted-offsets, right=shifted+offsets,
            heights=levels,
        )))

    for i, fit in enumerate(fits):
        if fit is None or (run and fit.step * fits[run[-1]].step <= 0):
            emit()
            run = []
        if fit is not None:
            run.append(i)
    emit()
    return runs


def prepare(tile, vector, paths):
    """Complete WFS 66634 lines use raw class 2/12, never the already blended path grid."""
    if vector is None or not paths:
        return []
    rows = frame(vector, tile.bbox.buffered(3))
    lines = [(str(r.feature_key), shapely.force_2d(r.geometry_local))
             for r in rows.itertuples() if str(r.kplkoodi) == "66634"]
    result = []
    survey = {p.stem: p for p in paths}
    store = TileStore(source_paths=survey)
    for key, geometry in lines:
        for line in shapely.get_parts(geometry):
            if line.geom_type != "LineString" or line.length < 1.5:
                continue
            bounds = C.BBoxLocal(*line.buffer(4).bounds)
            if not all(b.name in survey for b in C.laz_tiles_for_bbox(bounds)):
                continue
            cloud = store.query(bounds).query(classes=[2, 12], last_return=True)
            for index, (edge, profile) in enumerate(measure(line, ground_points(cloud))):
                result.append((edge, {"barrier": "retaining_wall", "_survey": profile,
                                      "source": f"wfs:{key}:{index}"}, False))
    return result
