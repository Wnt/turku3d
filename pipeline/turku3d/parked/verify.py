"""Validate the published AOI placements and collision primitives; reports stay in the sandbox."""

import json
import subprocess

from shapely.geometry import shape

from turku3d import config as C
from turku3d.layers import layer_root, read_collision, read_manifest
from turku3d.package.assemble import collision_tile
from turku3d.parked.__main__ import sha
from turku3d.parked.placement import footprint


def main():
    root = layer_root() / "traffic_static"
    manifest = read_manifest(root)
    if sha(root / "placements.json") != manifest["inputs"]["placements.json"]:
        raise ValueError("Placement audit hash differs from published manifest")
    report = json.loads((root / "placements.json").read_text())
    excluded = shape(report["exclusions"])
    cars = report["cars"]
    if len({c["object_id"] for c in cars}) != len(cars):
        raise ValueError("Duplicate car IDs")
    polygons = [footprint(c["x"], c["z"], c["yaw"], c["width"], c["length"]) for c in cars]
    for i, polygon in enumerate(polygons):
        if excluded.intersects(polygon):
            raise ValueError(f"Access obstruction: {cars[i]['object_id']}")
        if any(polygon.distance(other) < 0.55 for other in polygons[i + 1 :]):
            raise ValueError(f"Overlapping cars: {cars[i]['object_id']}")
    layers = {
        "traffic_static": {
            n.removesuffix(".col.npz"): read_collision(root / n)
            for n in manifest["tiles"]
            if n.endswith(".col.npz")
        }
    }
    out = C.work_root() / "reports" / "parked" / "collision"
    out.mkdir(parents=True, exist_ok=True)
    # Only this validator owns these report fixtures; remove stale tiles on a smaller rebuild.
    for old in out.glob("*.col"):
        old.unlink()
    ids = set(layers["traffic_static"])
    for tid in sorted(ids):
        data, _ = collision_tile(
            C.CollisionTile.parse(tid), layers, {"traffic_static": manifest}, ids
        )
        (out / f"{tid}.col").write_bytes(data)
    result = subprocess.run(
        ["node", str(C.REPO_ROOT / "tools/validator/parked.mjs"), str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    physics = json.loads(result.stdout.strip().splitlines()[-1])
    if physics["boxes"] != len(cars):
        raise ValueError("Published primitive count differs from placement count")
    physics.update(
        cars=len(cars),
        access_obstructions=0,
        overlaps=0,
        models=sorted({c["model"] for c in cars}),
        colors=sorted({c["color"] for c in cars}),
    )
    (out.parent / "physics.json").write_text(json.dumps(physics, indent=2) + "\n")
    print(json.dumps(physics))


if __name__ == "__main__":
    main()
