"""Local overlap-class lidar is provisional height evidence, never residual truth."""
import hashlib

import laspy
import numpy as np
import shapely

from turku3d import config as C


def overlap_points(paths, buildings, cache, input_hash):
    footprint = shapely.union_all([b.footprint for b in buildings])
    key = hashlib.sha256(input_hash.encode() + footprint.wkb).hexdigest()
    output = cache/(key+'.overlap-heights.npy')
    if output.exists():
        return np.load(output)
    chunks = []
    for path in paths:
        bounds = C.LazTile.parse(path.stem).bbox_en.to_local()
        if not shapely.box(*bounds.as_tuple()).intersects(footprint):
            continue
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(1_000_000):
                keep = np.asarray(chunk.classification) == 12
                x, y, z = C.en_to_local(np.asarray(chunk.x)[keep], np.asarray(chunk.y)[keep],
                                        np.asarray(chunk.z)[keep])
                inside = shapely.intersects_xy(footprint, x, z)
                if inside.any():
                    chunks.append(np.column_stack((x[inside], y[inside], z[inside])))
    points = np.concatenate(chunks) if chunks else np.empty((0, 3))
    np.save(output, points)
    return points


def supported_plane(footprint, points, base):
    """Seek a broad, near-horizontal surface under vegetation, with fixed RANSAC.

    Class 12 has no reliable building/vegetation semantic label. Even a supported
    plane is only a provisional height proxy, explicitly recorded as such.
    """
    keep = shapely.intersects_xy(footprint, points[:, 0], points[:, 2]) & (points[:, 1] > base+1.5)
    q = points[keep]
    if len(q) < 20:
        return None
    xy = q[:, [0, 2]] - np.asarray(footprint.centroid.coords)[0]
    design = np.column_stack((xy, np.ones(len(q))))
    rng = np.random.default_rng(103)
    best = np.zeros(len(q), dtype=bool)
    for _ in range(1000):
        sample = rng.choice(len(q), 3, replace=False)
        matrix = design[sample]
        if abs(np.linalg.det(matrix)) < .01:
            continue
        plane = np.linalg.solve(matrix, q[sample, 1])
        if np.linalg.norm(plane[:2]) > .5:
            continue
        support = np.abs(q[:, 1] - design @ plane) < .08
        if support.sum() > best.sum():
            best = support
    support = q[best]
    if len(support) < max(20, len(q)*.1):
        return None
    area_ratio = shapely.MultiPoint(support[:, [0, 2]]).convex_hull.area / footprint.area
    if area_ratio < .5:
        return None
    return support, {'plane_support_area_ratio': float(area_ratio),
                     'plane_support_fraction': float(best.mean()), 'plane_tolerance_m': .08,
                     'height_semantics': 'overlap class may contain vegetation; provisional plane proxy'}
