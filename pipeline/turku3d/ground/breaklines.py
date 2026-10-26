"""Measured breakline interchange shared with lidar detectors and structures."""

import json

import numpy as np

from .curbs import Curb


def load_breaklines(path):
    """Read world-local x/z polylines with absolute N2000 heights on both sides."""
    records = json.loads(path.read_text())
    if not isinstance(records, list):
        raise ValueError("Breaklines must be a JSON list")
    result = []
    seen = set()
    for record in records:
        object_id = record["object_id"]
        if not isinstance(object_id, str) or not object_id or object_id in seen:
            raise ValueError("Breakline object_id must be a unique nonempty string")
        seen.add(object_id)
        p, n, h = [
            np.asarray(record[k], dtype=np.float64)
            for k in ("centers_xz", "normals_xz", "side_heights")
        ]
        if p.ndim != 2 or p.shape[1] != 2 or len(p) < 2 or n.shape != p.shape or h.shape != p.shape:
            raise ValueError(f"{object_id}: expected matching (N,2) arrays, N >= 2")
        if not all(np.isfinite(a).all() for a in (p, n, h)):
            raise ValueError(f"{object_id}: coordinates must be finite")
        if not np.allclose(np.linalg.norm(n, axis=1), 1, atol=1e-6, rtol=0):
            raise ValueError(f"{object_id}: normals must have unit length")
        lengths = np.linalg.norm(np.diff(p, axis=0), axis=1)
        if np.any(lengths <= 0):
            raise ValueError(f"{object_id}: consecutive vertices must differ")
        if lengths.sum() < 0.05:
            continue
        kind = record["kind"]
        if kind not in ("curb", "curb_dropped", "crossing", "quay", "wall"):
            raise ValueError(f"{object_id}: unsupported kind {kind}")
        d = np.r_[0, np.cumsum(lengths)]
        # QA resamples independent held-out returns; supplied side heights are not truth.
        validation = []
        for along in np.arange(1.0, d[-1], 2.0):
            center = [np.interp(along, d, p[:, axis]) for axis in (0, 1)]
            normal = np.array([np.interp(along, d, n[:, axis]) for axis in (0, 1)])
            normal /= np.linalg.norm(normal)
            validation.append(
                {"distance": float(along), "center": center, "normal": normal.tolist()}
            )
        result.append(Curb(object_id, d, d, p, n, h, np.zeros(len(p)), kind, validation))
    return result


def clamp_curb_heights(curbs, minimum=0.06, maximum=0.20):
    """Keep measured curb midpoints and sign while bounding the playable rise."""
    for curb in curbs:
        if curb.kind != "curb":
            continue
        midpoint = curb.heights.mean(axis=1)
        rise = np.clip(np.abs(np.diff(curb.heights, axis=1)[:, 0]), minimum, maximum)
        direction = np.where(curb.heights[:, 1] >= curb.heights[:, 0], 1, -1)
        curb.heights[:, 0] = midpoint - direction * rise / 2
        curb.heights[:, 1] = midpoint + direction * rise / 2
    return curbs


def records(curbs):
    return [
        {
            "object_id": c.object_id,
            "kind": c.kind,
            "centers_xz": c.centers.tolist(),
            "side_heights": c.heights.tolist(),
            "normals_xz": c.normals.tolist(),
        }
        for c in curbs
    ]


def validation_geojson(curbs):
    """Declared fit steps for independent downstream collision reproduction checks."""
    features = []
    for curb in curbs:
        if curb.kind == "quay":
            continue
        for i in range(len(curb.centers) - 1):
            # Signed: a segment whose high side swaps between its ends has no
            # mean step in the interpolated mesh, whatever its end magnitudes.
            step = abs(float(np.diff(curb.heights[i : i + 2], axis=1).mean()))
            if not 0.03 <= step <= 1.0:
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {"id": f"{curb.object_id}:{i}", "step": step},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": curb.centers[i : i + 2].tolist(),
                    },
                }
            )
    return {"type": "FeatureCollection", "turku3d_coords": "engine-xz", "features": features}
