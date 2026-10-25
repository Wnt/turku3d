"""Python -> TypeScript round-trip cases for the .col reader.

    python -m turku3d.col.roundtrip --out <dir>

Writes ``<case>.col`` files with the Python writer and ``expected.json`` describing what any reader
must decode: per case the header fields, the canonical triangle list (tile-local float32 bit
patterns, rotated so the smallest corner comes first, sorted, with material) and world-local
float64 corners for the 2 mm check, plus primitives and META. The TS test
(tools/validator/test/roundtrip.test.ts) reads both and compares.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from turku3d.col import materials as M
from turku3d.col.format import ColTile, Primitive, canonical_triangles, read_col, write_col


def grid_mesh(rng, n, size=62.5, base=2.0, amp=0.5):
    xs = np.linspace(0, size, n)
    gx, gz = np.meshgrid(xs, xs)
    y = base + amp * rng.random(gx.shape)
    pos = np.stack([gx, y, gz], -1).reshape(-1, 3)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [(a, a + n, a + 1), (a + 1, a + n, a + n + 1)]
    return pos, np.array(tris, np.uint32)


def cases():
    rng = np.random.default_rng(20261025)
    shippable = [m.id for m in M.table() if m.id > 0]
    out = []

    pos, tri = grid_mesh(rng, 40)
    mats = rng.choice(shippable, len(tri))
    prims = [Primitive.box(M.mat_id("fence"), (3, 2.6, 4), (1.5, 0.6, 0.05), (0, 0.3826834, 0, 0.9238795)),
             Primitive.capsule(M.mat_id("tree_trunk"), (10, 4, 12), 2.0, 0.3),
             Primitive.cylinder(M.mat_id("pole"), (20, 5, 30), 3.0, 0.08)]
    meta = {"tile": "c_1_2", "built": "2026-10-25T12:00:00Z", "notes": "round-trip ä ö"}
    out.append(("grid_meshopt_v0", ColTile(1, 2, pos, tri, mats, prims, meta=meta), dict(encoding="meshopt")))
    out.append(("grid_meshopt_v1", ColTile(1, 2, pos, tri, mats, prims), dict(encoding="meshopt", vertex_version=1)))
    out.append(("grid_raw", ColTile(1, 2, pos, tri, mats, prims, meta=meta), dict(encoding="raw")))
    out.append(("grid_meshopt_noopt", ColTile(1, 2, pos, tri, mats), dict(encoding="meshopt", optimize=False)))

    # negative tile indices, world-local positions with sub-mm detail, curb material -> FIX flag
    pos2, tri2 = grid_mesh(rng, 25, amp=0.003)
    world = pos2 + np.array([-3 * 62.5, 0, -1 * 62.5])
    mats2 = np.full(len(tri2), M.mat_id("curb"), np.uint8)
    out.append(("negative_tile", ColTile.for_tile(-3, -1, world, tri2, mats2, aoi_border=True), dict(encoding="meshopt")))

    # large tile (~150k triangle budget scale)
    pos3, tri3 = grid_mesh(rng, 250, amp=1.0)
    mats3 = rng.choice([M.mat_id("road"), M.mat_id("sidewalk"), M.mat_id("grass"), M.mat_id("curb")], len(tri3))
    out.append(("large", ColTile(4, 5, pos3, tri3, mats3), dict(encoding="meshopt")))

    # empty mesh, primitives only
    out.append(("prims_only", ColTile(0, 0, np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0),
                                      [Primitive.box(M.mat_id("street_furniture"), (1, 1, 1), (0.5, 0.4, 0.2))]),
                dict(encoding="meshopt")))
    return out


def describe(t: ColTile) -> dict:
    canon = canonical_triangles(t)
    o = np.asarray(t.origin)
    tri_bits, tri_world, tri_mat = [], [], []
    for corners, m in canon:
        tri_bits.append([c for v in corners for c in v])
        f = np.array(corners, np.uint32).view(np.float32).astype(np.float64) + o
        tri_world.append([round(float(c), 9) for c in f.reshape(-1)])
        tri_mat.append(m)
    return {
        "id": t.id,
        "tile": [t.tile_i, t.tile_j],
        "origin": list(t.origin),
        "flags": t.flags,
        "vertex_count": int(len(t.positions)),
        "triangle_count": int(len(t.indices)),
        "bbox_min": [float(np.float32(v)) for v in t.bbox_min],
        "bbox_max": [float(np.float32(v)) for v in t.bbox_max],
        "triangles_bits": tri_bits,
        "triangles_world": tri_world,
        "triangles_material": tri_mat,
        "primitives": [{"kind": p.kind, "material": p.material, "center": [float(np.float32(v)) for v in p.center],
                        "rotation": [float(np.float32(v)) for v in p.rotation],
                        "params": [float(np.float32(v)) for v in p.params]} for p in t.primitives],
        "meta": t.meta,
        "encodings": {s["tag"]: s["encoding"] for s in t.sections},
    }


def write_cases(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    expected = {}
    for name, tile, kw in cases():
        data = write_col(tile, out / f"{name}.col", **kw)
        back = read_col(data)  # the Python reader must agree with itself before TS is asked
        if canonical_triangles(back) != canonical_triangles(tile):
            raise AssertionError(f"{name}: Python round trip differs")
        expected[name] = describe(back)
    (out / "expected.json").write_text(json.dumps(expected))
    return expected


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    exp = write_cases(Path(a.out))
    for k, v in exp.items():
        print(f"{k}: {v['triangle_count']} tris, {len(v['primitives'])} prims, encodings {v['encodings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
