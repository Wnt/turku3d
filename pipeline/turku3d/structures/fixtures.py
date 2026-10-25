"""Tiny generated meshes consumed by Python and the real Rapier validator tests."""
import argparse
import json
from pathlib import Path
import numpy as np
from .geometry import stairs, profile_wall, ramp, quay
from turku3d.col.materials import mat_id


def scenes():
    result = {}
    for angle in (10, 25, 39, 40):
        rise = 4 * np.tan(np.radians(angle))
        meshes, meta = stairs(f"stairs-{angle}", [0, 0, 0], [4, rise, 0], 2)
        result[f"stairs-{angle}"] = (meshes, meta)
    for height in (0.31, 0.35, 1.2):
        result[f"wall-{height}"] = ([profile_wall("wall", [[2, -3], [2, 3]], [0, 0], [height, height])], {})
    result["fence"] = ([profile_wall("fence", [[2, -3], [2, 3]], [0, 0], [1.2, 1.2],
                                              thickness=0.08, material="fence")], {})
    result["quay"] = (quay("quay", [[2, -3], [2, 3]], [-3, -3], [0, 0]), {})
    result["ramp"] = ([ramp("ramp", [[0, 0, -2], [0, 0, 2], [4, 1, 2], [4, 1, -2]],
                                    [[0, 1, 2], [0, 2, 3]])], {"bottom": [0, 0, 0], "top": [4, 1, 0]})
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    data = {key: {"meshes": [{"positions": m.positions.tolist(), "indices": m.indices.tolist(),
                            "material": mat_id(m.material)} for m in meshes], "meta": meta}
            for key, (meshes, meta) in scenes().items()}
    a.out.write_text(json.dumps(data) + "\n")


if __name__ == "__main__":
    main()
