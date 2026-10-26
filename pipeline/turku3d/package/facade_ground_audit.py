"""Check approved office facade photo bottoms against the surveyed sidewalk."""

import json
import math
from pathlib import Path

import numpy as np

from turku3d import config as C


def ground_height(ground, x, z, cache):
    name = (
        f"c_{math.floor(x / C.COLLISION_TILE_SIZE)}_{math.floor(z / C.COLLISION_TILE_SIZE)}.col.npz"
    )
    if name not in cache:
        with np.load(ground / name) as layer:
            cache[name] = layer["positions"][layer["indices"]]
    triangles = cache[name]
    a = triangles[:, 0, [0, 2]]
    b = triangles[:, 1, [0, 2]] - a
    c = triangles[:, 2, [0, 2]] - a
    q = np.array([x, z]) - a
    determinant = b[:, 0] * c[:, 1] - c[:, 0] * b[:, 1]
    valid = np.abs(determinant) > 1e-10
    divisor = np.where(valid, determinant, 1)
    u = (q[:, 0] * c[:, 1] - c[:, 0] * q[:, 1]) / divisor
    v = (b[:, 0] * q[:, 1] - q[:, 0] * b[:, 1]) / divisor
    hits = np.flatnonzero(valid & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6))
    if not len(hits):
        raise ValueError(f"No ground triangle at ({x:.2f}, {z:.2f})")
    i = hits[0]
    return float(
        triangles[i, 0, 1]
        + u[i] * (triangles[i, 1, 1] - triangles[i, 0, 1])
        + v[i] * (triangles[i, 2, 1] - triangles[i, 0, 1])
    )


def audit(work, radius=75, tolerance=0.3):
    layers = work / "build/layers"
    assembly = work / "textures/assembly"
    approved = set(json.loads((assembly / "MANIFEST.json").read_text())["bound"])
    features = json.loads((layers / "buildings/facades.geojson").read_text())["features"]
    cache = {}
    checked = []
    for feature in features:
        props = feature["properties"]
        facade_id = f"{props['building_id']}_{props['facade_index']}"
        if facade_id not in approved:
            continue
        east, north = np.mean(feature["geometry"]["coordinates"], axis=0)
        if math.hypot(east - C.OFFICE_E, north - C.OFFICE_N) > radius:
            continue
        meta = json.loads(
            (
                assembly / "facades" / props["building_id"] / f"{props['facade_index']}.json"
            ).read_text()
        )
        bottom_v = meta["uv"]["offset"][1]
        top_v = bottom_v + meta["uv"]["scale"][1] * (props["top_height"] - props["base_height"])
        x = east - C.ORIGIN_E + props["normal"][0] * 0.4
        z = C.ORIGIN_N - north + props["normal"][2] * 0.4
        height = ground_height(layers / "ground", x, z, cache)
        error = abs(props["base_height"] - height)
        if error > tolerance or abs(bottom_v - 1) > 0.02 or abs(top_v) > 0.02:
            raise AssertionError(
                f"{facade_id}: ground error {error:.3f} m, photo v={bottom_v:.3f}..{top_v:.3f}"
            )
        checked.append(error)
    if len(checked) < 20:
        raise AssertionError(f"Only {len(checked)} approved office facades sampled")
    return {"sampled": len(checked), "max_ground_error_m": max(checked)}


if __name__ == "__main__":
    import os

    print(json.dumps(audit(Path(os.environ["TURKU3D_WORK"]))))
