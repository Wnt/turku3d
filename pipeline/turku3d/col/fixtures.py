"""Synthetic .col fixtures for the Rapier validator (tools/validator) and the round-trip tests.

    python -m turku3d.col.fixtures --out tools/validator/fixtures

Writes deterministic files (byte-identical on every run):

good/                      must PASS every validator check
  c_1_2.col                12 cm / 25 cm / 40 cm curb platforms, a stair flight (sloped collider +
                           query-only tread helpers + side walls + landing), two buildings (one rotated),
                           a water sensor, pole/tree/fence primitives. meshopt-encoded.
  c_2_2.col                plain ground sharing the seam x = 125 with c_1_2, one building. RAW.
  footprints.geojson       building footprints, engine [x, z] world-local coordinates
  curbs.geojson            curb lines (platform edges) for the --curbs mode
neg_hole/                  2.5 m x 5 m hole in the ground          -> holes + belowGround FAIL
neg_open_building/         building with its south wall missing     -> buildings FAIL
neg_steep_stair/           stair at 53 deg (rise 2 m over 1.5 m)    -> stairs FAIL
neg_material0/             one ground triangle with material 0       -> format FAIL
expected.json              the expected status per check and fixture (read by the validator tests)
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col import materials as M
from turku3d.col.format import ColTile, Primitive, write_col

S = C.COLLISION_TILE_SIZE
Y0 = 2.0  # base ground height (N2000-ish)
MAT = {m.name: m.id for m in M.table()}


class Mesh:
    """Indexed triangle mesh in tile-local coordinates with exact vertex sharing."""

    def __init__(self):
        self.v: list[tuple[float, float, float]] = []
        self.vid: dict[tuple, int] = {}
        self.t: list[tuple[int, int, int]] = []
        self.m: list[int] = []

    def vert(self, p) -> int:
        k = tuple(float(np.float32(c)) for c in p)
        if k not in self.vid:
            self.vid[k] = len(self.v)
            self.v.append(k)
        return self.vid[k]

    def tri(self, a, b, c, mat: int, outward=None, inside=None):
        a, b, c = (np.asarray(p, np.float64) for p in (a, b, c))
        n = np.cross(b - a, c - a)
        if inside is not None:
            outward = (a + b + c) / 3 - np.asarray(inside, np.float64)
        if outward is not None and np.dot(n, outward) < 0:
            b, c = c, b
        self.t.append((self.vert(a), self.vert(b), self.vert(c)))
        self.m.append(mat)

    def quad(self, p0, p1, p2, p3, mat: int, outward=None, inside=None):
        self.tri(p0, p1, p2, mat, outward, inside)
        self.tri(p0, p2, p3, mat, outward, inside)

    def box(self, x0, x1, y0, y1, z0, z1, side: int, top: int, skip=(), bottom=False):
        """Axis-aligned box. ``skip`` names faces to leave out: -x +x -z +z."""
        c = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        self.quad((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1), top, inside=c)
        if bottom:
            self.quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), side, inside=c)
        faces = {
            "-x": ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)),
            "+x": ((x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)),
            "-z": ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)),
            "+z": ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)),
        }
        for k, f in faces.items():
            if k not in skip:
                self.quad(*f, side, inside=c)

    def prism(self, base: list, height_top: list, y_bottom: float, side: int, top: int):
        """Vertical prism over a convex polygon `base` [(x,z)], top heights per corner."""
        n = len(base)
        cx = sum(p[0] for p in base) / n
        cz = sum(p[1] for p in base) / n
        cy = (y_bottom + sum(height_top) / n) / 2
        inside = (cx, cy, cz)
        tops = [(x, h, z) for (x, z), h in zip(base, height_top)]
        for k in range(1, n - 1):
            self.tri(tops[0], tops[k], tops[k + 1], top, outward=(0, 1, 0))
        for k in range(n):
            (xa, za), (xb, zb) = base[k], base[(k + 1) % n]
            self.quad((xa, y_bottom, za), (xb, y_bottom, zb), tops[(k + 1) % n], tops[k], side, inside=inside)

    def ground(self, cell=2.5, mat_fn=lambda x, z: MAT["road"], holes=()):
        n = int(round(S / cell))
        for r in range(n):
            for c in range(n):
                x0, z0 = c * cell, r * cell
                if any(hx0 <= x0 < hx1 and hz0 <= z0 < hz1 for hx0, hx1, hz0, hz1 in holes):
                    continue
                mid = mat_fn(x0 + cell / 2, z0 + cell / 2)
                self.quad((x0, Y0, z0), (x0 + cell, Y0, z0), (x0 + cell, Y0, z0 + cell), (x0, Y0, z0 + cell),
                          mid, outward=(0, 1, 0))

    def tile(self, i, j, prims=(), meta=None) -> ColTile:
        return ColTile(i, j, np.array(self.v, np.float32), np.array(self.t, np.uint32),
                       np.array(self.m, np.uint8), list(prims), meta=meta)


# --------------------------------------------------------------------------------------
# Scene description (tile-local coordinates of c_1_2)
# --------------------------------------------------------------------------------------

PLATFORMS = [  # (name, x0, x1, z0, z1, height)
    ("curb_12cm", 4.0, 14.0, 10.0, 18.0, 0.12),
    ("curb_25cm", 20.0, 30.0, 10.0, 18.0, 0.25),
    ("step_40cm", 36.0, 46.0, 10.0, 18.0, 0.40),
]
STAIR = dict(x0=50.0, x1=54.0, z0=6.0, run=4.0, rise=2.0)
BUILDING_A = (10.0, 22.0, 30.0, 42.0, 9.0)  # x0 x1 z0 z1 height
BUILDING_B = dict(center=(40.0, 48.0), size=(8.0, 6.0), angle_deg=30.0, height=6.0)
BUILDING_C = (20.0, 30.0, 25.0, 33.0, 7.0)  # in c_2_2


def ground_mat(x, z):
    if z < 20:
        return MAT["road"]
    if x > 48:
        return MAT["grass"]
    return MAT["sidewalk"]


def rotated_rect(center, size, angle_deg):
    cx, cz = center
    hx, hz = size[0] / 2, size[1] / 2
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + ca * x - sa * z, cz + sa * x + ca * z) for x, z in ((-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz))]


def build_stair(m: Mesh, run: float, rise: float, treads=True):
    x0, x1, z0 = STAIR["x0"], STAIR["x1"], STAIR["z0"]
    z1 = z0 + run
    ytop = Y0 + rise
    # single inclined plane (stair_slope), normal up
    m.quad((x0, Y0, z0), (x1, Y0, z0), (x1, ytop, z1), (x0, ytop, z1), MAT["stair_slope"], outward=(0, 1, 0))
    # query-only tread helpers (visual treads), riser count = round(rise / 0.16)
    if treads:
        nr = max(1, round(rise / 0.16))
        dr, dz = rise / nr, run / nr
        for k in range(nr):
            y = Y0 + (k + 1) * dr
            za, zb = z0 + k * dz, z0 + (k + 1) * dz
            m.quad((x0 + 0.01, y, za), (x1 - 0.01, y, za), (x1 - 0.01, y, zb), (x0 + 0.01, y, zb),
                   MAT["stair_tread_helper"], outward=(0, 1, 0))
            m.quad((x0 + 0.01, y - dr, za), (x1 - 0.01, y - dr, za), (x1 - 0.01, y, za), (x0 + 0.01, y, za),
                   MAT["stair_tread_helper"], outward=(0, 0, -1))
    # side walls: thin prisms whose top follows the slope + 0.9 m
    for wx0, wx1 in ((x0 - 0.15, x0), (x1, x1 + 0.15)):
        m.prism([(wx0, z0), (wx1, z0), (wx1, z1), (wx0, z1)], [Y0 + 0.9, Y0 + 0.9, ytop + 0.9, ytop + 0.9],
                Y0, MAT["wall"], MAT["wall"])
    # landing (top paving, wall sides); the front face under the slope's top edge is omitted
    m.box(x0 - 1.0, x1 + 1.0, Y0, ytop, z1, z1 + 6.0, MAT["wall"], MAT["paving_slab"], skip=("-z",))


def scene_c_1_2(variant: str = "good") -> ColTile:
    m = Mesh()
    holes = [(30.0, 32.5, 50.0, 55.0)] if variant == "neg_hole" else []
    m.ground(mat_fn=ground_mat, holes=holes)
    if variant == "neg_material0":
        m.m[0] = 0
    for _, x0, x1, z0, z1, h in PLATFORMS:
        m.box(x0, x1, Y0, Y0 + h, z0, z1, MAT["curb"], MAT["sidewalk"])
    if variant == "neg_steep_stair":
        build_stair(m, run=1.5, rise=2.0)
    else:
        build_stair(m, run=STAIR["run"], rise=STAIR["rise"])
    bx0, bx1, bz0, bz1, bh = BUILDING_A
    m.box(bx0, bx1, Y0, Y0 + bh, bz0, bz1, MAT["building"], MAT["roof"],
          skip=("-z",) if variant == "neg_open_building" else ())
    rb = rotated_rect(BUILDING_B["center"], BUILDING_B["size"], BUILDING_B["angle_deg"])
    m.prism(rb, [Y0 + BUILDING_B["height"]] * 4, Y0, MAT["building"], MAT["roof"])
    # water sensor surface (not solid): walkers pass through, holes check ignores it
    m.quad((52.0, Y0 + 0.3, 30.0), (60.0, Y0 + 0.3, 30.0), (60.0, Y0 + 0.3, 40.0), (52.0, Y0 + 0.3, 40.0),
           MAT["water"], outward=(0, 1, 0))
    prims = [
        Primitive.cylinder(MAT["pole"], (30.0, Y0 + 2.0, 24.0), 2.0, 0.08),
        Primitive.capsule(MAT["tree_trunk"], (5.0, Y0 + 2.5, 52.0), 2.0, 0.25),
        Primitive.box(MAT["fence"], (52.5, Y0 + 0.6, 58.0), (7.5, 0.6, 0.05)),
    ]
    meta = {"tile": "c_1_2", "fixture": variant, "notes": "synthetic validator fixture"}
    return m.tile(1, 2, prims, meta)


def scene_c_2_2() -> ColTile:
    m = Mesh()
    m.ground(mat_fn=lambda x, z: MAT["road"] if z < 20 else MAT["grass"])
    bx0, bx1, bz0, bz1, bh = BUILDING_C
    m.box(bx0, bx1, Y0, Y0 + bh, bz0, bz1, MAT["building"], MAT["roof"])
    return m.tile(2, 2, meta={"tile": "c_2_2", "fixture": "good"})


def footprints_geojson() -> dict:
    o12 = np.array([1 * S, 2 * S])
    o22 = np.array([2 * S, 2 * S])

    def rect(x0, x1, z0, z1, o):
        return [[float(x + o[0]), float(z + o[1])] for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0))]

    bx0, bx1, bz0, bz1, _ = BUILDING_A
    rb = rotated_rect(BUILDING_B["center"], BUILDING_B["size"], BUILDING_B["angle_deg"])
    ring_b = [[float(x + o12[0]), float(z + o12[1])] for x, z in rb + rb[:1]]
    cx0, cx1, cz0, cz1, _ = BUILDING_C
    feats = [
        ("A", rect(bx0, bx1, bz0, bz1, o12)),
        ("B", ring_b),
        ("C", rect(cx0, cx1, cz0, cz1, o22)),
    ]
    return {
        "type": "FeatureCollection",
        "turku3d_coords": "engine-xz",
        "features": [{"type": "Feature", "properties": {"id": n},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}} for n, ring in feats],
    }


def curbs_geojson() -> dict:
    o = np.array([1 * S, 2 * S])
    feats = []
    for name, x0, x1, z0, z1, h in PLATFORMS:
        ring = [(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]
        feats.append({"type": "Feature", "properties": {"id": name, "step": h},
                      "geometry": {"type": "LineString",
                                   "coordinates": [[float(x + o[0]), float(z + o[1])] for x, z in ring]}})
    return {"type": "FeatureCollection", "turku3d_coords": "engine-xz", "features": feats}


EXPECTED = {
    "good": {"pass": True, "checks": {"format": "pass", "holes": "pass", "buildings": "pass",
                                      "curbs": "pass", "stairs": "pass", "belowGround": "pass"},
             "curb_steps": {"curb_12cm": "climb", "curb_25cm": "climb", "step_40cm": "block"}},
    "neg_hole": {"pass": False, "checks": {"holes": "fail", "belowGround": "fail", "buildings": "pass"}},
    "neg_open_building": {"pass": False, "checks": {"buildings": "fail", "holes": "pass"}},
    "neg_steep_stair": {"pass": False, "checks": {"stairs": "fail", "holes": "pass"}},
    "neg_material0": {"pass": False, "checks": {"format": "fail"}},
}


def write_all(out: Path) -> list[Path]:
    out = Path(out)
    written = []

    def put(path: Path, data: bytes | str):
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_text(data)
        else:
            path.write_bytes(data)
        written.append(path)

    fp = json.dumps(footprints_geojson(), indent=1) + "\n"
    cb = json.dumps(curbs_geojson(), indent=1) + "\n"
    c22 = write_col(scene_c_2_2(), encoding="raw")
    for variant in EXPECTED:
        d = out / variant
        strict = variant != "neg_material0"
        enc = "raw" if variant == "neg_material0" else "meshopt"
        put(d / "c_1_2.col", write_col(scene_c_1_2(variant), encoding=enc, strict=strict))
        put(d / "c_2_2.col", c22)
        put(d / "footprints.geojson", fp)
        put(d / "curbs.geojson", cb)
    put(out / "expected.json", json.dumps(EXPECTED, indent=1) + "\n")
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(C.REPO_ROOT / "tools" / "validator" / "fixtures"))
    a = ap.parse_args(argv)
    for p in write_all(Path(a.out)):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
