"""Read-only before/after material audit on retained surveyed facade segments.

This evaluates generated paint instructions, not colour classification of stored maps
or historical served-pixel telemetry. No tile, texture or source asset is written.
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from turku3d import config as C
from turku3d.facade_assets import building, legacy_rows, root
from turku3d.facade_kit import MATERIALS, renderer
from turku3d.facade_program_geometry import wall_parts
from turku3d.facade_programs import compile_wall, resolve


def audit():
    renderer()
    from turkutex.facade_derive import building_attributes

    index = root() / "index.json"
    keys = (
        sorted(json.loads(index.read_text()).get("buildings", {}))
        if index.exists()
        else sorted(legacy_rows())
    )
    attributes = building_attributes(C.raw_dir("wfs/work-3x3"), keys)
    rows = {}
    for key in keys:
        facades = building(key)["facades"]
        quads = []
        for f in facades:
            a, b = np.asarray(f["coordinates"], float)[[0, -1], :2]
            base, height = f["base_height"], f["metadata"]["height_m"]
            quad = np.array(
                [
                    [a[0] - C.ORIGIN_E, base, C.ORIGIN_N - a[1]],
                    [b[0] - C.ORIGIN_E, base, C.ORIGIN_N - b[1]],
                    [b[0] - C.ORIGIN_E, base + height, C.ORIGIN_N - b[1]],
                    [a[0] - C.ORIGIN_E, base + height, C.ORIGIN_N - a[1]],
                ]
            )
            normal = f.get("normal")
            if (
                normal is not None
                and np.dot(np.array(normal)[[0, 2]], (quad[1] - quad[0])[[2, 0]] * [1, -1]) < 0
            ):
                quad = quad[[1, 0, 3, 2]]
            quads.append(quad)
        if not quads:
            continue
        vertices = np.concatenate(quads)
        footprint = shapely.MultiPoint(vertices[:, [0, 2]]).convex_hull.buffer(0.01)
        b = SimpleNamespace(
            key=key,
            facade_key=key,
            footprint=footprint,
            base=float(vertices[:, 1].min()),
            top=float(vertices[:, 1].max()),
            attributes=attributes.get(key, {}),
        )
        program = resolve(b, vertices, {})
        materials, wall_materials, finishes = set(), set(), set()
        for quad, w in zip(quads, program["walls"], strict=True):
            style = compile_wall(w, program["storeys"]["value"])
            style["origin"][1] = b.base
            wall_materials.add(
                MATERIALS[style["module"] // 6 if style["module"] < 42 else style["module"] - 42]
            )
            finishes.add((style["wall"], style["trim"]))
            for _, _, s in wall_parts(quad, w, style):
                # Metal shutters (module 48) are openings, not facade cladding.
                if s["module"] == 48 and s["wall"] == "#55534e":
                    continue
                materials.add(MATERIALS[s["module"] // 6 if s["module"] < 42 else s["module"] - 42])
        rows[key] = dict(
            segments=len(quads),
            materials=sorted(materials),
            walls=sorted(wall_materials),
            finishes=[list(f) for f in sorted(finishes)],
            reviewed=sum(bool(w.get("measured_wall_id")) for w in program["walls"]),
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    rows = audit()
    result = dict(
        buildings=len(rows),
        mixed_materials=sum(len(r["materials"]) > 1 for r in rows.values()),
        mixed_walls=sum(len(r["walls"]) > 1 for r in rows.values()),
        rows=rows,
    )
    if args.baseline:
        before = json.loads(args.baseline.read_text())["rows"]
        changed = [k for k, r in rows.items() if before.get(k) != r]
        mixed_changed = [k for k in changed if len(before[k]["materials"]) > 1]
        result.update(
            changed=changed,
            mixed_changed=mixed_changed,
            mixed_resolved=[k for k in mixed_changed if len(rows[k]["materials"]) == 1],
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {k: len(v) if isinstance(v, list) else v for k, v in result.items() if k != "rows"}
        )
    )


if __name__ == "__main__":
    main()
