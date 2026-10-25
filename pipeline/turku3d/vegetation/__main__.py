"""Publish the vegetation layer from shared city vectors and classified lidar."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.layers import (
    PRIMITIVE_DTYPE,
    CollisionLayer,
    layer_root,
    write_collision,
    write_manifest,
)
from turku3d.vegetation.trees import digest, load_points, measure, register
from turku3d.vegetation.visual import ASSETS, write_instances


def publish(trees, out, inputs, params):
    out.mkdir(parents=True, exist_ok=True)
    files, budgets = [], {}
    for tile in sorted({C.VisualTile.at(t.x, t.z) for t in trees}):
        batch = [t for t in trees if C.VisualTile.at(t.x, t.z) == tile]
        name = tile.id + ".vis.glb"
        budgets[tile.id] = write_instances(out / name, tile, batch)
        files.append(name)
    for tile in sorted({C.CollisionTile.at(t.x, t.z) for t in trees}):
        batch = [t for t in trees if C.CollisionTile.at(t.x, t.z) == tile]
        primitives = np.zeros(len(batch), PRIMITIVE_DTYPE)
        for p, t in zip(primitives, batch, strict=True):
            p["kind"], p["material"] = "cylinder", 26
            p["height"] = min(t.height * 0.55, 8)
            p["center"] = [t.x, t.y + p["height"] / 2, t.z]
            p["radius"] = t.trunk_radius
        name = tile.id + ".col.npz"
        write_collision(
            out / name,
            CollisionLayer(
                np.empty((0, 3), np.float64),
                np.empty((0, 3), np.uint32),
                np.empty(0, np.uint8),
                primitives,
            ),
        )
        files.append(name)
    code = {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")}
    inputs.update({str(p): digest(p) for p in ASSETS.glob("*") if p.is_file()})
    report = {
        "trees": len(trees),
        "measured": sum(t.measured for t in trees),
        "sources": dict(Counter(t.source for t in trees)),
        "tiles": budgets,
        "trees_without_lidar": [t.object_id for t in trees if not t.measured],
        "records": [t.record() for t in trees],
    }
    (out / "trees.json").write_text(json.dumps(report, indent=2) + "\n")
    write_manifest(
        out,
        inputs=inputs,
        params={
            **params,
            "seams": "objects",
            "budgets": budgets,
            "inventory_sha256": digest(out / "trees.json"),
        },
        code=json.dumps(code, sort_keys=True),
        tiles=files,
    )
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=layer_root() / "vegetation")
    ap.add_argument("--scope", choices=("office-block", "phase1"), default="office-block")
    args = ap.parse_args()
    started = time.monotonic()
    bounds = (
        C.OFFICE_BLOCK.with_margin
        if args.scope == "office-block"
        else C.BBoxLocal(0, 0, 2 * C.VISUAL_TILE_SIZE, 2 * C.VISUAL_TILE_SIZE).buffered(
            C.MESH_MARGIN
        )
    )
    trees, inputs = register(bounds)
    ground, canopy, lidar_inputs = load_points(bounds)
    inputs.update(lidar_inputs)
    print(f"Measuring {len(trees)} mapped trees against {len(canopy)} canopy returns", flush=True)
    trees = measure(trees, ground, canopy, bounds)
    report = publish(
        trees,
        args.out,
        inputs,
        {
            "bounds": bounds.as_tuple(),
            "deduplicate_m": 2,
            "canopy_class": 5,
            "season": "autumn",
            "licence": "CC0-1.0",
        },
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("records", "trees_without_lidar")},
            indent=2,
        )
    )
    print(f"Published {args.out}; {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
