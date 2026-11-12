"""Buildings, their walls and an occlusion scene from the register's LoD2 surfaces (raw/wfs).

A wall is a run of consecutive, collinear WallSurface polygons on one ring of the footprint. Seen from outside,
`a` is its left end and `b` its right end; `x` runs from `a` along the wall in metres and `z` is N2000 height. Every
quantity here is read from the source data at run time: nothing is specific to a building.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from turku3d import config

MERGE_ANGLE_DEG = 6.0  # consecutive surfaces within this turn form one wall
MERGE_GAP_M = 0.35  # and must meet end to end within this
ADJOIN_M = 0.6  # another footprint this close to a wall adjoins it
INVALID_IDS = {"", "None", "nan", "0", "<NA>"}


@dataclass
class Wall:
    id: str
    building: str
    index: int
    a: np.ndarray  # (E, N) left end seen from outside
    b: np.ndarray  # (E, N) right end
    base: float  # N2000 of the wall's foot (lowest)
    eaves: float  # N2000 where the wall's top is lowest (the eaves of a gable wall)
    top: float  # N2000 of the wall's highest point (the apex of a gable wall)
    profile: list  # [(x_m, z)] of the top edge, left to right
    ring: int  # 0 = outer ring, 1.. = courtyard rings
    adjoining: list = field(default_factory=list)  # [{building, x0, x1, eaves}] neighbours built against it

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.b - self.a))

    @property
    def tangent(self) -> np.ndarray:
        return (self.b - self.a) / max(self.length, 1e-9)

    @property
    def normal(self) -> np.ndarray:
        t = self.tangent
        return np.array([t[1], -t[0]])

    @property
    def bearing(self) -> float:
        """Compass bearing the wall faces."""
        n = self.normal
        return math.degrees(math.atan2(n[0], n[1])) % 360

    @property
    def height(self) -> float:
        return self.top - self.base

    def point(self, x: float, z: float) -> np.ndarray:
        e, n = self.a + self.tangent * x
        return np.array([e, n, z])

    def exposed(self, min_height: float = 2.0) -> list:
        """[(x0, x1, z0)] parts of the wall not hidden by an adjoining building; z0 is where the free wall starts."""
        cuts = sorted({0.0, self.length, *(c for j in self.adjoining for c in (j["x0"], j["x1"]))})
        out = []
        for x0, x1 in zip(cuts, cuts[1:], strict=False):
            if x1 - x0 < 0.3:
                continue
            mid = (x0 + x1) / 2
            z0 = max([self.base] + [j["eaves"] for j in self.adjoining if j["x0"] <= mid <= j["x1"]])
            if self.eaves - z0 >= min_height:
                if out and abs(out[-1][2] - z0) < 0.5 and abs(out[-1][1] - x0) < 1e-6:
                    out[-1] = (out[-1][0], x1, out[-1][2])
                else:
                    out.append((x0, x1, z0))
        return out

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "index": self.index,
            "a": [round(float(v), 3) for v in self.a],
            "b": [round(float(v), 3) for v in self.b],
            "length_m": round(self.length, 2),
            "bearing_deg": round(self.bearing, 1),
            "base_h": round(self.base, 2),
            "eaves_h": round(self.eaves, 2),
            "top_h": round(self.top, 2),
            "ring": self.ring,
            "adjoining": self.adjoining,
        }


@dataclass
class Building:
    id: str  # permanent building identifier of the register, else the LoD2 feature key
    feature_key: str
    footprint: Polygon
    walls: list
    register: dict  # address, year, storeys, height, use: priors only
    triangles: np.ndarray  # (T, 3, 3) wall and roof surfaces, for occlusion

    @property
    def centre(self) -> np.ndarray:
        c = self.footprint.representative_point()
        return np.array([c.x, c.y])

    @property
    def base(self) -> float:
        return min((w.base for w in self.walls), default=0.0)

    @property
    def top(self) -> float:
        return float(self.triangles[:, :, 2].max()) if len(self.triangles) else self.base


def lod2_files(bounds) -> list[Path]:
    import pyarrow.parquet as pq

    out = []
    for path in sorted(config.raw_dir("wfs").glob("*/bldg_Building_LOD2.parquet")):
        schema = pq.read_schema(path)
        if "surface_type" not in schema.names:
            continue
        box = json.loads(schema.metadata[b"geo"])["columns"]["geometry"].get("bbox")
        if box and box[0] <= bounds[2] and box[2] >= bounds[0] and box[1] <= bounds[3] and box[3] >= bounds[1]:
            out.append(path)
    return out


def _triangles(rings) -> np.ndarray:
    import mapbox_earcut

    rings = [r[:-1] if np.array_equal(r[0], r[-1]) else r for r in rings]
    if any(len(r) < 3 for r in rings):
        return np.zeros((0, 3, 3))
    points = np.concatenate(rings)
    local = rings[0] - rings[0][0]
    normal = np.cross(local, np.roll(local, -1, axis=0)).sum(0)
    if np.linalg.norm(normal) < 1e-8 or not np.isfinite(points).all():
        return np.zeros((0, 3, 3))
    flat = np.ascontiguousarray(np.delete(points - points[0], np.argmax(np.abs(normal)), axis=1))
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    return points[mapbox_earcut.triangulate_float64(flat, ends).reshape(-1, 3)]


def _surface(polygon) -> dict | None:
    """One WallSurface polygon as a 2D segment with its top profile, or None when it is not a vertical plane."""
    pts = np.asarray(polygon.exterior.coords)
    if pts.shape[1] != 3 or len(pts) < 4:
        return None
    xy = pts[:-1, :2]
    origin = xy.mean(0)
    _, _, vt = np.linalg.svd(xy - origin, full_matrices=False)
    t = vt[0]
    if np.max(np.abs((xy - origin) @ np.array([t[1], -t[0]]))) > 0.2:
        return None
    s = (pts[:, :2] - origin) @ t
    if np.ptp(s) < 0.05 or np.ptp(pts[:, 2]) < 0.5:
        return None
    return {"p0": origin + s.min() * t, "p1": origin + s.max() * t, "s0": float(s.min()), "t": t, "origin": origin,
            "sz": np.column_stack([s, pts[:, 2]])}


def _top_profile(parts, a, tangent, length) -> tuple[float, list]:
    """Lowest foot and the top edge [(x, z)] of merged surfaces, sampled along the wall."""
    polys, base = [], math.inf
    for p in parts:
        x = (p["origin"] - a) @ tangent + p["sz"][:, 0] * float(p["t"] @ tangent)
        base = min(base, float(p["sz"][:, 1].min()))
        poly = shapely.make_valid(Polygon(np.column_stack([x, p["sz"][:, 1]])))
        if not poly.is_empty:
            polys.append(poly)
    merged = shapely.union_all(polys) if polys else None
    profile = []
    n = max(3, min(41, int(length / 0.5) + 1))
    for x in np.linspace(0.02 * length, 0.98 * length, n):
        if merged is None:
            break
        cut = merged.intersection(LineString([(x, base - 5), (x, base + 500)]))
        if not cut.is_empty:
            profile.append((round(float(x), 2), round(float(cut.bounds[3]), 2)))
    return base, profile


def _walls(key: str, building_id: str, footprint, surfaces) -> list[Wall]:
    rings = [footprint.exterior, *footprint.interiors] if footprint.geom_type == "Polygon" else [
        r for g in footprint.geoms for r in (g.exterior, *g.interiors)]
    outer = {id(g.exterior) for g in (footprint.geoms if footprint.geom_type != "Polygon" else [footprint])}
    placed = {}
    for s in surfaces:
        mid = Point(*((s["p0"] + s["p1"]) / 2))
        k = min(range(len(rings)), key=lambda i: rings[i].distance(mid))
        if rings[k].distance(mid) > 1.0:
            continue
        # Orient: seen from outside, a is on the left. The footprint lies behind the wall.
        t = s["p1"] - s["p0"]
        t = t / np.linalg.norm(t)
        n = np.array([t[1], -t[0]])
        probe = np.array([mid.x, mid.y])
        inside_front = footprint.contains(Point(*(probe + 0.3 * n)))
        inside_back = footprint.contains(Point(*(probe - 0.3 * n)))
        if inside_front and not inside_back:
            s = dict(s, p0=s["p1"], p1=s["p0"])
        elif inside_front == inside_back:
            continue  # an interior partition or a surface off the footprint
        placed.setdefault(k, []).append((rings[k].project(mid), s))
    walls = []
    for k, items in sorted(placed.items()):
        items.sort(key=lambda it: it[0])
        runs = [[s] for _, s in items]  # chained by matching ends, whatever the ring's winding
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for i, run in enumerate(runs):
                for j, other in enumerate(runs):
                    if i == j:
                        continue
                    t0 = run[-1]["p1"] - run[-1]["p0"]
                    t1 = other[0]["p1"] - other[0]["p0"]
                    cos = float(t0 @ t1 / (np.linalg.norm(t0) * np.linalg.norm(t1)))
                    if (np.linalg.norm(run[-1]["p1"] - other[0]["p0"]) <= MERGE_GAP_M
                            and cos >= math.cos(math.radians(MERGE_ANGLE_DEG))):
                        runs[i] = run + other
                        del runs[j]
                        changed = True
                        break
                if changed:
                    break
        ring_no = 0 if id(rings[k]) in outer else k
        for run in runs:
            a, b = run[0]["p0"], run[-1]["p1"]
            length = float(np.linalg.norm(b - a))
            if length < 0.3:
                continue
            tangent = (b - a) / length
            base, profile = _top_profile(run, a, tangent, length)
            if not profile:
                continue
            tops = np.array([z for _, z in profile])
            walls.append(Wall("", building_id, 0, a, b, base, float(np.quantile(tops, 0.1)), float(tops.max()),
                              profile, ring_no))
    # A stable order: outer ring first, then clockwise from north by where the wall's middle lies around the
    # footprint, so an index means the same wall for the same source geometry.
    c = footprint.centroid

    def around(w):
        m = (w.a + w.b) / 2
        return math.degrees(math.atan2(m[0] - c.x, m[1] - c.y)) % 360

    walls.sort(key=lambda w: (w.ring, around(w)))
    for i, w in enumerate(walls):
        w.index = i
        w.id = f"{building_id}:w{i:02d}"
    return walls


def _valid(value) -> bool:
    return value is not None and str(value) not in INVALID_IDS


def _register(row) -> dict:
    def num(name, kind=float):
        v = row.get(name)
        try:
            return None if v is None or str(v) in INVALID_IDS else kind(float(v))
        except (TypeError, ValueError):
            return None

    year = num("yearOfConstruction", int)
    if year is None and _valid(row.get("valmistunut")):
        try:
            year = int(str(row["valmistunut"])[-4:])
        except ValueError:
            year = None
    return {
        "address": row.get("taydellinen_osoite_fi") if _valid(row.get("taydellinen_osoite_fi")) else None,
        "year": year,
        "storeys": num("storeysAboveGround", int) or num("kerrosluku", int),
        "height_m": num("measuredHeight"),
        "function": str(row.get("function")) if _valid(row.get("function")) else None,
        "floor_area_m2": num("kerrosala"),
    }


class Scene:
    """Buildings within bounds, with footprints indexed for neighbours and triangles for occlusion."""

    def __init__(self, bounds):
        import pandas as pd

        self.bounds = tuple(float(v) for v in bounds)
        files = lod2_files(self.bounds)
        self.fingerprint = hashlib.sha256(repr([(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files]
                                               ).encode()).hexdigest()[:16]
        columns = ["feature_key", "geom_index", "geometry", "geom_type", "surface_type", "pysyvarakennustunnus",
                   "taydellinen_osoite_fi", "yearOfConstruction", "valmistunut", "storeysAboveGround", "kerrosluku",
                   "measuredHeight", "function", "kerrosala"]
        frames = [pd.read_parquet(p, columns=columns) for p in files]
        self.buildings: dict[str, Building] = {}
        if not frames:
            return
        frame = pd.concat(frames, ignore_index=True).drop_duplicates(["feature_key", "geom_index"])
        frame = frame[(frame.geom_type == "Polygon") & frame.surface_type.isin(["GroundSurface", "WallSurface",
                                                                                "RoofSurface"])]
        geoms = shapely.from_wkb(frame.geometry.values)
        box = shapely.bounds(geoms)
        keep = (box[:, 0] <= self.bounds[2]) & (box[:, 2] >= self.bounds[0]) & (box[:, 1] <= self.bounds[3]) & (
            box[:, 3] >= self.bounds[1])
        keys = set(frame.feature_key.values[keep])
        frame = frame.assign(geom=geoms)[frame.feature_key.isin(keys)]
        for key, group in frame.groupby("feature_key", sort=True):
            ground = [shapely.force_2d(g).buffer(0) for g in group.geom[group.surface_type == "GroundSurface"]]
            if not ground:
                continue
            footprint = shapely.union_all(ground)
            if footprint.is_empty or footprint.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            row = group.iloc[0].to_dict()
            pid = str(row.get("pysyvarakennustunnus"))
            building_id = pid if pid not in INVALID_IDS else str(key)
            surfaces = [s for g in group.geom[group.surface_type == "WallSurface"] if (s := _surface(g))]
            tris = [_triangles([np.asarray(r.coords) for r in [g.exterior, *g.interiors]])
                    for g in group.geom[group.surface_type != "GroundSurface"]
                    if np.asarray(g.exterior.coords).shape[1] == 3]
            triangles = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
            if building_id in self.buildings:  # two LoD2 features of one register building: keep the larger
                if self.buildings[building_id].footprint.area >= footprint.area:
                    continue
            self.buildings[building_id] = Building(building_id, str(key), footprint,
                                                   _walls(str(key), building_id, footprint, surfaces),
                                                   _register(row), triangles)
        self._index()

    def _index(self):
        self.ids = list(self.buildings)
        self.tree = shapely.STRtree([self.buildings[i].footprint for i in self.ids])
        owner, tris = [], []
        for k, i in enumerate(self.ids):
            t = self.buildings[i].triangles
            tris.append(t)
            owner.append(np.full(len(t), k))
        self.triangles = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
        self.owner = np.concatenate(owner) if owner else np.zeros(0, int)
        lo, hi = self.triangles[:, :, :2].min(1), self.triangles[:, :, :2].max(1)
        self.tri_tree = shapely.STRtree(shapely.box(lo[:, 0], lo[:, 1], hi[:, 0], hi[:, 1]))
        for b in self.buildings.values():
            for w in b.walls:
                w.adjoining = self._adjoining(b, w)

    def _adjoining(self, building: Building, wall: Wall) -> list:
        """Neighbouring buildings standing against the wall: where along it, and how high they reach there."""
        line = LineString([wall.a + wall.normal * ADJOIN_M / 2, wall.b + wall.normal * ADJOIN_M / 2])
        out = []
        for k in self.tree.query(line.buffer(ADJOIN_M)):
            other = self.buildings[self.ids[k]]
            if other is building:
                continue
            hit = line.intersection(other.footprint.buffer(ADJOIN_M / 2))
            for part in getattr(hit, "geoms", [hit]):
                if part.is_empty or part.geom_type != "LineString" or part.length < 0.5:
                    continue
                xs = sorted(float((np.array(c) - wall.a) @ wall.tangent) for c in part.coords)
                near = [w for w in other.walls if LineString([w.a, w.b]).distance(part) < 2 * ADJOIN_M]
                eaves = max((w.eaves for w in near), default=other.top)
                out.append({"building": other.id, "x0": round(max(0.0, xs[0]), 2),
                            "x1": round(min(wall.length, xs[-1]), 2), "eaves": round(float(eaves), 2)})
        return sorted(out, key=lambda j: j["x0"])

    @cached_property
    def footprints(self) -> list:
        return [self.buildings[i].footprint for i in self.ids]

    def nearest(self, e: float, n: float, within: float = 15.0) -> Building | None:
        p = Point(e, n)
        near = [self.buildings[self.ids[k]] for k in self.tree.query(p.buffer(within))]
        near = [b for b in near if b.footprint.distance(p) <= within]
        return min(near, key=lambda b: b.footprint.distance(p)) if near else None

    def within(self, e: float, n: float, radius: float) -> list[Building]:
        p = Point(e, n)
        return [b for k in self.tree.query(p.buffer(radius))
                if (b := self.buildings[self.ids[k]]).footprint.distance(p) <= radius]

    def cast(self, origin: np.ndarray, targets: np.ndarray, *, stop_short: float = 0.4):
        """For rays origin -> targets: distance of the first surface hit before the target (inf when free) and the
        index into self.ids of the building hit (-1). `stop_short` ignores hits in the last metres of each ray."""
        origin = np.asarray(origin, float)
        targets = np.asarray(targets, float).reshape(-1, 3)
        rays = targets - origin
        lengths = np.linalg.norm(rays, axis=1)
        best = np.full(len(targets), np.inf)
        who = np.full(len(targets), -1)
        if not len(self.triangles) or not len(targets):
            return best, who
        lo = np.minimum(origin[:2], targets[:, :2].min(0))
        hi = np.maximum(origin[:2], targets[:, :2].max(0))
        idx = self.tri_tree.query(shapely.box(*lo, *hi))
        limit = np.maximum(lengths - stop_short, 0) / np.maximum(lengths, 1e-9)
        for start in range(0, len(idx), 256):
            sel = idx[start:start + 256]
            tri = self.triangles[sel]
            e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
            cross = np.cross(rays[:, None], e2[None])
            det = np.einsum("rti,ti->rt", cross, e1)
            inv = np.divide(1, det, out=np.zeros_like(det), where=np.abs(det) > 1e-9)
            off = origin - tri[:, 0]
            u = np.einsum("ti,rti->rt", off, cross) * inv
            q = np.cross(off, e1)
            v = np.einsum("ri,ti->rt", rays, q) * inv
            t = np.einsum("ti,ti->t", e2, q)[None] * inv
            hit = (np.abs(det) > 1e-9) & (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7) & (t > 1e-4) & (
                t < limit[:, None])
            t = np.where(hit, t, np.inf)
            k = t.argmin(1)
            tk = t[np.arange(len(t)), k] * lengths
            better = tk < best
            best[better] = tk[better]
            who[better] = self.owner[sel[k[better]]]
        return best, who


def scene_around(e: float, n: float, radius: float) -> Scene:
    return Scene((e - radius, n - radius, e + radius, n + radius))
