"""Exercise published accepted programs through production painting without tile builds."""

import argparse
import copy
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
from turku3d import config as C
from turku3d.facade_program_geometry import wall_parts
from turku3d.facade_programs import compile_wall, overlay
from turku3d.facade_sections import prepare
from turkutex.facade_archetypes import sample
from turkutex.facade_learned_layout import expand
from turkutex.facade_learning import apply


def check(wall, floors):
    width, height = wall["width_m"], wall["height_m"]
    g = wall.get("geometry", {})
    coords = g.get("coordinates") or [g["p0"], g["p1"]]
    vertices = np.array(
        [
            [coords[0][0] - C.ORIGIN_E, g.get("base_h", 0), C.ORIGIN_N - coords[0][1]],
            [coords[1][0] - C.ORIGIN_E, g.get("base_h", 0), C.ORIGIN_N - coords[1][1]],
        ]
        * 2,
        float,
    )
    vertices[2:] = vertices[[1, 0]] + [0, height, 0]
    wall = copy.deepcopy(wall)
    if not wall.get("learned_layout") or wall.get("measured_geometry"):
        wall.update(measured_geometry=g, measured_size=[width, height])
    style = compile_wall(wall, floors)
    style["origin"][1] = g.get("base_h", 0)
    parts = list(wall_parts(vertices, wall, style))
    assert parts, wall["wall_id"]
    for points, uv, instruction in parts:
        assert np.isfinite(points).all() and np.isfinite(uv).all()
        assert points[:, 1].min() >= g.get("base_h", 0) - 1e-5
        assert points[:, 1].max() <= g.get("base_h", 0) + height + 1e-5
        assert all(
            np.isfinite(instruction[k]).all() for k in ("opening", "bands", "detail", "size")
        )
    return len(parts)


def audit(source, model):
    started = time.monotonic()
    counts = Counter()
    worst = []
    for p in json.loads(source.read_text()):
        p = prepare(p)
        floors = p["storeys"]["value"]
        for own in p["walls"]:
            if own.get("observation", {}).get("needs_review", True):
                counts["unresolved_preserved"] += 1
                continue
            base = sample(
                p["building_id"],
                p["attributes"],
                [{k: own[k] for k in ("wall_id", "width_m", "height_m")}],
            )["walls"][0]
            overlay(base, own)
            parts = check(base, floors)
            counts["painted_walls"] += 1
            counts["parts"] += parts
            worst.append((parts, own["wall_id"]))
        # Exercise unknown identities at actual observed dimensions, without photo overlays.
        own = max(p["walls"], key=lambda w: w["width_m"])
        generated = apply(
            sample(
                "audit:" + p["building_id"],
                p["attributes"],
                [{k: own[k] for k in ("wall_id", "width_m", "height_m")}],
            ),
            model,
        )
        w = generated["walls"][0]
        if w.get("learned_layout"):
            expand(w, generated["storeys"]["value"], own["geometry"])
        w["geometry"] = own["geometry"]
        counts["learned_parts"] += check(w, generated["storeys"]["value"])
        counts["learned_buildings"] += 1
    return dict(
        counts=counts,
        seconds=round(time.monotonic() - started, 3),
        largest=sorted(worst, reverse=True)[:10],
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source", type=Path, default=C.REPO_ROOT / "textures/library/facade-programs.json"
    )
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    result = audit(args.source, json.loads(args.model.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
