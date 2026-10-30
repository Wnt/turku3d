"""Check shipped primitive colliders against decoded visible geometry, without a browser."""

import json
import subprocess
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from turku3d.col.format import PRIM_BOX, read_col
from turku3d.col.materials import table


def audit(root, manifest):
    root = Path(root)
    script = Path(__file__).parents[1] / "package/collision_visual.mjs"
    visual = root / manifest["visual"][manifest["tile"]]["url"]
    result = subprocess.run(
        ["node", str(script), str(visual)], capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    points, bounds = {}, {}
    for draw in json.loads(result.stdout):
        material = draw["material"]
        vertices = np.asarray(draw["v"]).reshape(-1, 3)
        points.setdefault(material, []).append(vertices)
        for instance in np.split(vertices, draw["instances"]):
            bounds.setdefault(material, []).append((instance.min(axis=0), instance.max(axis=0)))
    trees = {name: cKDTree(np.concatenate(parts)) for name, parts in points.items()}
    names = {m.id: m.name for m in table()}
    counts, missing = {}, []
    for cid, entry in manifest["collision"].items():
        col = read_col((root / entry["url"]).read_bytes())
        for index, primitive in enumerate(col.primitives):
            name = names[primitive.material]
            counts[name] = counts.get(name, 0) + 1
            center = np.asarray(primitive.center) + col.origin
            if primitive.kind == PRIM_BOX:
                corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
                corners = corners * primitive.params[:3]
                q = np.asarray(primitive.rotation)
                cross = np.cross(q[:3], corners)
                corners = corners + 2 * (q[3] * cross + np.cross(q[:3], cross)) + center
                matched = name in trees and trees[name].query(corners)[0].max() < 0.2
            else:
                # Trunk prototypes can taper; require a visible trunk at the complete solid envelope.
                lo, hi = primitive.aabb()
                lo, hi = lo + col.origin, hi + col.origin
                matched = any(
                    np.all(low - 0.35 <= lo) and np.all(high + 0.35 >= hi)
                    for low, high in bounds.get(name, [])
                )
            if not matched:
                missing.append(
                    dict(cell=cid, primitive=index, material=name, center=center.tolist())
                )
    return dict(primitives=sum(counts.values()), counts=counts, unmatched=missing)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    result = audit(args.manifest.parent, json.loads(args.manifest.read_text()))
    print(json.dumps(result))
    raise SystemExit(bool(result["unmatched"]))
