"""Golden projection fixture shared by pytest (Python camera) and the node stub test (JS camera)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qalib.camera import project  # noqa: E402

GOLDEN = Path(__file__).with_name("golden_projection.json")
W, H = 800, 600
POSES = [
    {
        "E": 23459676.0,
        "N": 6704242.7,
        "H": 5.6,
        "yaw": 324.8,
        "pitch": 12.0,
        "roll": 0.0,
        "fov": 60.0,
    },
    {
        "E": 23459647.3,
        "N": 6704228.1,
        "H": 5.9,
        "yaw": 38.6,
        "pitch": -5.0,
        "roll": 7.0,
        "fov": 45.0,
    },
    {
        "E": 23459703.5,
        "N": 6704258.7,
        "H": 5.7,
        "yaw": 262.4,
        "pitch": 3.0,
        "roll": -4.0,
        "fov": 75.0,
    },
]
POINTS = [
    (23459668.0, 6704254.0, 8.0),
    (23459650.0, 6704265.0, 13.0),
    (23459690.0, 6704250.0, 3.5),
    (23459730.0, 6704282.0, 12.0),
    (23459618.0, 6704236.0, 11.0),
]


def build():
    cases = []
    for p in POSES:
        for e, n, h in POINTS:
            r = project(p, W, H, e, n, h)
            cases.append(
                {
                    "pose": p,
                    "point": [e, n, h],
                    "px": None if r is None else [round(r[0], 4), round(r[1], 4)],
                }
            )
    return {"width": W, "height": H, "cases": cases}


if __name__ == "__main__":
    GOLDEN.write_text(json.dumps(build(), indent=1))
    print(GOLDEN)
