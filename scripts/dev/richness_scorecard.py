#!/usr/bin/env python3
"""Street richness scorecard: counts at the fixed street cameras, from tile data only.

Reads the coarse tiles the client streams (``<root>/<tile>/manifest.json`` plus the visual GLB
and ``roads.json`` it names), either over HTTP from the published service (read-only GETs, the
same URLs the client uses) or from a local tile directory with the same layout (``--tiles``).
Nothing is rendered and nothing is written outside ``--json``/``--markdown``/``--cache``.

Per camera (``game/src/qa/street-cameras.json``) it reports two views:

* ``radius``: everything whose horizontal position lies within 50 m of the camera;
* ``per_100m``: counts inside the camera's street corridor (a flat-capped band of
  ``half_width_m`` on both sides of the street polyline), divided by the stretch length / 100.

Categories, all from tile data:

* ``lamps``: props with ``prop_type`` lamp (none today: bridge-deck lamps are merged into the
  bridge furniture mesh and tunnel light strips are not street lamps, so neither is counted);
* ``furniture``: props of the kinds in ``FURNITURE`` plus instanced bollards, by kind;
* ``trees``: vegetation instances that are not bushes, deduplicated over their mesh parts;
* ``parked_cars``: the manifest's ``vehicles``;
* ``sign_poles``: props with ``prop_type`` sign or bus_stop (the client's breakable poles);
* ``paint``: road-paint triangles, as m² and as metres of a standard 0.12 m line (m² / 0.12);
* ``road_m``: length of the tile street graph (``roads.json``), split car / foot-only;
* ``moving_cars`` / ``walkers``: 0 until those systems exist; ``--spawner-state FILE`` feeds
  positions in (``{"moving_cars": [[x, z], ...], "walkers": [[x, z], ...]}``).

Tile seams: objects are deduplicated by identity across tiles, and each tile's street graph and
paint are clipped to the tile's own square, so a feature on a seam is counted once.

Examples::

    python3 scripts/dev/richness_scorecard.py --cache /tmp/rs \\
        --json /tmp/rs/baseline.json --markdown /tmp/rs/baseline.md
    python3 scripts/dev/richness_scorecard.py --tiles ~/turku3d-local/tiles --camera riverside
    python3 scripts/dev/richness_scorecard.py --ground 168.2 246.1   # ground height for a camera
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))
from turku3d.col.meshopt import decode_index_buffer, decode_vertex_buffer  # noqa: E402

TILE = 250.0
RADIUS = 50.0
PAINT_LINE_WIDTH = 0.12
DEFAULT_URL = os.environ.get("VITE_DRAFT_URL", "https://turku.lab.madekivi.fi/coarse/")
CAMERAS = REPO / "game/src/qa/street-cameras.json"
TARGETS = Path(__file__).with_name("richness_targets.toml")
FURNITURE = ("bench", "bin", "bike_rack", "bike_station", "parking_meter", "bollard", "post")
POLE_KINDS = ("sign", "bus_stop")
LAMP_KINDS = ("lamp", "street_lamp")
CAR, FOOT = 2, 1
COLUMNS = (
    "lamps",
    "furniture",
    "trees",
    "parked_cars",
    "sign_poles",
    "paint_line_m",
    "road_car_m",
    "moving_cars",
    "walkers",
)


# --------------------------------------------------------------------------------------------
# Geometry (engine x/z metres; unit-tested in scripts/dev/tests/test_richness_scorecard.py)
# --------------------------------------------------------------------------------------------


def tile_of(x: float, z: float) -> str:
    return f"t0_{math.floor(x / TILE)}_{math.floor(z / TILE)}"


def tile_square(tile: str) -> tuple[float, float, float, float]:
    _, i, j = tile.split("_")
    return int(i) * TILE, int(j) * TILE, (int(i) + 1) * TILE, (int(j) + 1) * TILE


def in_square(points: np.ndarray, square) -> np.ndarray:
    """Half-open ownership test: a point on a shared edge belongs to exactly one tile."""
    x0, z0, x1, z1 = square
    p = np.asarray(points, float).reshape(-1, 2)
    return (p[:, 0] >= x0) & (p[:, 0] < x1) & (p[:, 1] >= z0) & (p[:, 1] < z1)


def within_radius(points, center, radius=RADIUS) -> np.ndarray:
    p = np.asarray(points, float).reshape(-1, 2)
    return np.hypot(p[:, 0] - center[0], p[:, 1] - center[1]) <= radius


def segment_in_circle(a, b, center, radius) -> float:
    """Length of segment a-b inside the disc."""
    a, b, c = (np.asarray(v, float) for v in (a, b, center))
    d = b - a
    length = float(np.hypot(*d))
    if length == 0:
        return 0.0
    f = a - c
    qa, qb, qc = d @ d, 2 * (f @ d), f @ f - radius * radius
    disc = qb * qb - 4 * qa * qc
    if disc <= 0:
        return 0.0
    root = math.sqrt(disc)
    t0, t1 = (-qb - root) / (2 * qa), (-qb + root) / (2 * qa)
    lo, hi = max(t0, 0.0), min(t1, 1.0)
    return max(hi - lo, 0.0) * length


def polyline_in_circle(points, center, radius=RADIUS) -> float:
    p = np.asarray(points, float).reshape(-1, 2)
    return sum(segment_in_circle(p[i], p[i + 1], center, radius) for i in range(len(p) - 1))


def clip_segment(a, b, square):
    """Liang-Barsky clip of segment a-b to an axis-aligned square; None when outside."""
    x0, z0, x1, z1 = square
    (ax, az), (bx, bz) = a, b
    dx, dz = bx - ax, bz - az
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dz, az - z0), (dz, z1 - az)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return None
    if hi - lo <= 0:
        return None
    return (ax + lo * dx, az + lo * dz), (ax + hi * dx, az + hi * dz)


def clip_polyline(points, square) -> list[tuple]:
    """Segments of a polyline inside a square (each tile owns only its own square)."""
    p = np.asarray(points, float).reshape(-1, 2)
    out = []
    for i in range(len(p) - 1):
        seg = clip_segment(tuple(p[i]), tuple(p[i + 1]), square)
        if seg is not None:
            out.append(seg)
    return out


@dataclass
class Corridor:
    """Both sides of a street: points within half_width of the polyline, between its end caps."""

    polyline: np.ndarray
    half_width: float

    def __post_init__(self):
        self.polyline = np.asarray(self.polyline, float).reshape(-1, 2)
        if len(self.polyline) < 2:
            raise ValueError("a street corridor needs at least two points")
        seg = np.diff(self.polyline, axis=0)
        self._lengths = np.hypot(seg[:, 0], seg[:, 1])
        self._start = np.concatenate([[0.0], np.cumsum(self._lengths)[:-1]])

    @property
    def length(self) -> float:
        return float(self._lengths.sum())

    def locate(self, points) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(distance to the polyline, arc length of the nearest point, signed side) per point.
        Side > 0 is left of the polyline direction seen from above (engine x east, z south)."""
        p = np.asarray(points, float).reshape(-1, 2)
        a = self.polyline[:-1]
        d = np.diff(self.polyline, axis=0)
        rel = p[:, None, :] - a[None, :, :]
        t = np.clip((rel * d).sum(-1) / np.maximum(self._lengths**2, 1e-12), 0, 1)
        near = a[None] + t[..., None] * d[None]
        dist = np.hypot(*(p[:, None, :] - near).transpose(2, 0, 1))
        k = dist.argmin(axis=1)
        rows = np.arange(len(p))
        s = self._start[k] + t[rows, k] * self._lengths[k]
        cross = d[k, 0] * rel[rows, k, 1] - d[k, 1] * rel[rows, k, 0]
        # z grows south, so a positive x/z cross product is on the right-hand side when looking along d.
        return dist[rows, k], s, -np.sign(cross)

    def contains(self, points) -> np.ndarray:
        p = np.asarray(points, float).reshape(-1, 2)
        if not len(p):
            return np.zeros(0, bool)
        dist, _, _ = self.locate(p)
        a = self.polyline[:-1]
        d = np.diff(self.polyline, axis=0)
        # Flat end caps: the projection must fall inside the first/last segment's span.
        first = ((p - a[0]) @ d[0]) / max(self._lengths[0], 1e-12)
        last = ((p - a[-1]) @ d[-1]) / max(self._lengths[-1], 1e-12)
        inside_caps = (first >= 0) & (last <= self._lengths[-1])
        return (dist <= self.half_width) & inside_caps

    def sides(self, points) -> tuple[int, int]:
        p = np.asarray(points, float).reshape(-1, 2)
        if not len(p):
            return 0, 0
        inside = self.contains(p)
        _, _, side = self.locate(p[inside]) if inside.any() else (None, None, np.zeros(0))
        return int((side > 0).sum()), int((side <= 0).sum())


def per_100m(count: float, length: float) -> float | None:
    return round(count * 100.0 / length, 2) if length > 0 else None


# --------------------------------------------------------------------------------------------
# GLB reading (meshopt EXT_meshopt_compression, filters NONE; EXT_mesh_gpu_instancing)
# --------------------------------------------------------------------------------------------

COMPONENT = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Glb:
    """GLB reader. External buffers (``uri``, e.g. ``../shared/<sha>.bin``) are fetched lazily
    through ``resolve(uri)``; a meshopt ``fallback`` buffer carries no data."""

    def __init__(self, data: bytes, resolve=None):
        magic, _, _ = struct.unpack_from("<4sII", data, 0)
        if magic != b"glTF":
            raise ValueError("not a GLB")
        offset, self.json, self.bin = 12, None, b""
        while offset < len(data):
            length, kind = struct.unpack_from("<I4s", data, offset)
            chunk = data[offset + 8 : offset + 8 + length]
            if kind == b"JSON":
                self.json = json.loads(chunk)
            elif kind == b"BIN\x00":
                self.bin = chunk
            offset += 8 + length
        self._views: dict[int, bytes] = {}
        self._buffers: dict[int, bytes] = {}
        self._resolve = resolve

    def buffer(self, index: int) -> bytes:
        if index not in self._buffers:
            spec = self.json["buffers"][index]
            if "uri" in spec:
                if self._resolve is None:
                    raise ValueError(
                        f"buffer {index} is external ({spec['uri']}) and no resolver was given"
                    )
                data = self._resolve(spec["uri"])
                if data is None:
                    raise ValueError(f"buffer {index}: {spec['uri']} not found")
            elif spec.get("extensions", {}).get("EXT_meshopt_compression", {}).get("fallback"):
                raise ValueError(f"buffer {index} is a meshopt fallback without data")
            else:
                data = self.bin  # the GLB BIN chunk
            self._buffers[index] = data
        return self._buffers[index]

    def view(self, index: int) -> tuple[bytes, int]:
        bv = self.json["bufferViews"][index]
        ext = bv.get("extensions", {}).get("EXT_meshopt_compression")
        if index not in self._views:
            if ext:
                if ext.get("filter", "NONE") != "NONE":
                    raise ValueError(f"meshopt filter {ext['filter']} is not supported")
                start = ext.get("byteOffset", 0)
                src = self.buffer(ext["buffer"])[start : start + ext["byteLength"]]
                if ext["mode"] == "ATTRIBUTES":
                    out = decode_vertex_buffer(src, ext["count"], ext["byteStride"]).tobytes()
                elif ext["mode"] == "TRIANGLES":
                    # The decoder always returns uint32; the accessor reads the view's own width.
                    wide = {2: np.uint16, 4: np.uint32}[ext["byteStride"]]
                    out = (
                        decode_index_buffer(src, ext["count"], ext["byteStride"])
                        .astype(wide)
                        .tobytes()
                    )
                else:
                    raise ValueError(f"meshopt mode {ext['mode']} is not supported")
            else:
                start = bv.get("byteOffset", 0)
                out = self.buffer(bv["buffer"])[start : start + bv["byteLength"]]
            self._views[index] = out
        stride = bv.get("byteStride") or (ext or {}).get("byteStride") or 0
        return self._views[index], stride

    def accessor(self, index: int) -> np.ndarray:
        acc = self.json["accessors"][index]
        dtype = np.dtype(COMPONENT[acc["componentType"]])
        width = WIDTH[acc["type"]]
        count = acc["count"]
        if "bufferView" not in acc or count == 0:
            return np.zeros((count, width), dtype)
        raw, stride = self.view(acc["bufferView"])
        item = dtype.itemsize * width
        stride = stride or item
        base = acc.get("byteOffset", 0)
        buf = np.frombuffer(raw, np.uint8, count=(count - 1) * stride + item, offset=base)
        rows = np.lib.stride_tricks.as_strided(buf, shape=(count, item), strides=(stride, 1))
        out = np.ascontiguousarray(rows).view(dtype).reshape(count, width)
        if acc.get("normalized"):
            out = out.astype(np.float32) / float(np.iinfo(dtype).max)
        return out

    def nodes(self):
        """(node index, node, world 4x4 matrix) for every node reachable from the scene."""
        nodes = self.json.get("nodes", [])
        scene = self.json.get("scenes", [{}])[self.json.get("scene", 0)]
        stack = [(i, np.eye(4)) for i in scene.get("nodes", [])]
        while stack:
            i, parent = stack.pop()
            node = nodes[i]
            world = parent @ local_matrix(node)
            yield i, node, world
            stack.extend((c, world) for c in node.get("children", []))


def quat_matrix(q) -> np.ndarray:
    x, y, z, w = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def local_matrix(node) -> np.ndarray:
    if "matrix" in node:
        return np.asarray(node["matrix"], float).reshape(4, 4).T
    m = np.eye(4)
    m[:3, :3] = quat_matrix(node.get("rotation", (0, 0, 0, 1))) * np.asarray(
        node.get("scale", (1, 1, 1))
    )
    m[:3, 3] = node.get("translation", (0, 0, 0))
    return m


def transform(world: np.ndarray, points: np.ndarray) -> np.ndarray:
    p = np.asarray(points, float).reshape(-1, 3)
    return p @ world[:3, :3].T + world[:3, 3]


# --------------------------------------------------------------------------------------------
# Tile contents
# --------------------------------------------------------------------------------------------


@dataclass
class Item:
    category: str  # lamp | furniture | tree | parked_car | sign_pole | other
    kind: str
    ident: str
    x: float
    z: float
    y: float = 0.0


@dataclass
class TileData:
    tile: str
    items: list[Item] = field(default_factory=list)
    paint: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # centroid x, z, area m²
    roads: list[tuple[int, np.ndarray]] = field(default_factory=list)  # (flags, polyline x/z)
    ground: list[np.ndarray] = field(default_factory=list)  # world triangles (M, 3, 3)
    generation: str = ""
    pass_kind: str = ""
    missing: str = ""


def prop_category(kind: str) -> str:
    if kind in LAMP_KINDS:
        return "lamp"
    if kind in POLE_KINDS:
        return "sign_pole"
    if kind in FURNITURE:
        return "furniture"
    return "other"


def range_base(positions: np.ndarray, indices: np.ndarray, start: int, count: int):
    """The client's rangeBase: mean x/z of the lowest vertices of an index range."""
    used = np.unique(indices[start : start + count])
    if not len(used):
        return None
    p = positions[used]
    low = p[:, 1].min()
    base = p[p[:, 1] <= low + 0.001]
    return float(base[:, 0].mean()), float(low), float(base[:, 2].mean())


def read_visual(data: bytes, tile: str, out: TileData, *, ground=False, resolve=None):
    glb = Glb(data, resolve)
    js = glb.json
    trees: dict[str, Item] = {}
    for _, node, world in glb.nodes():
        e = node.get("extras", {}) or {}
        kind = e.get("kind")
        inst = node.get("extensions", {}).get("EXT_mesh_gpu_instancing")
        if inst and "TRANSLATION" in inst.get("attributes", {}):
            t = transform(world, glb.accessor(inst["attributes"]["TRANSLATION"]))
            ids = e.get("instance_object_ids") or []
            if kind == "vegetation":
                for n, p in enumerate(t):
                    ident = ids[n] if n < len(ids) else f"{e.get('variant')}@{p[0]:.1f},{p[2]:.1f}"
                    if str(ident).startswith("bush:"):
                        continue
                    trees.setdefault(
                        ident, Item("tree", str(e.get("variant", "tree")), ident, p[0], p[2], p[1])
                    )
            elif kind == "prop":
                name = str(e.get("object_id") or node.get("name") or "prop")
                category = "furniture" if name == "bollard" else "other"
                for p in t:
                    out.items.append(
                        Item(category, name, f"{name}@{p[0]:.2f},{p[2]:.2f}", p[0], p[2], p[1])
                    )
            continue
        if "mesh" not in node:
            continue
        mesh = js["meshes"][node["mesh"]]
        objects = e.get("objects")
        material = e.get("material_key") or e.get("material")
        is_paint = kind == "markings" or material == "road_paint"
        props = [o for o in objects or [] if o.get("prop_type")] if objects else []
        single = e.get("prop_type")
        is_ground = ground and kind == "ground" and material != "water" and not e.get("sensor")
        if not (is_paint or props or single or is_ground):
            continue
        for prim in mesh["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            pos = transform(world, glb.accessor(prim["attributes"]["POSITION"]))
            idx = glb.accessor(prim["indices"]).reshape(-1).astype(np.int64)
            if is_paint or is_ground:
                tri = pos[idx.reshape(-1, 3)]
                if is_ground:
                    out.ground.append(tri)
                    continue
                cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
                area = 0.5 * np.linalg.norm(cross, axis=1)
                c = tri.mean(axis=1)
                out.paint = np.vstack([out.paint, np.c_[c[:, 0], c[:, 2], area]])
                continue
            entries = props or [dict(e, start=0, count=len(idx))]
            for o in entries:
                base = range_base(pos, idx, int(o["start"]), int(o["count"]))
                if base is None:
                    continue
                k = str(o["prop_type"])
                ident = str(o.get("object_id") or o.get("id") or f"{k}@{base[0]:.2f},{base[2]:.2f}")
                out.items.append(Item(prop_category(k), k, ident, base[0], base[2], base[1]))
    out.items.extend(trees.values())


def read_roads(data: bytes, out: TileData):
    graph = json.loads(data)
    nodes = np.asarray(graph["nodes"], float).reshape(-1, 2)
    for edge in graph["edges"]:
        a, b, flags = int(edge[0]), int(edge[1]), int(edge[2])
        interior = np.asarray(edge[4:], float).reshape(-1, 2)
        out.roads.append((flags, np.vstack([nodes[a], interior, nodes[b]])))


def read_vehicles(fragment: dict, out: TileData):
    for v in fragment.get("vehicles") or []:
        ident = str(v.get("object_id") or f"car@{v['x']:.2f},{v['z']:.2f}")
        out.items.append(
            Item(
                "parked_car",
                str(v.get("model", "car")),
                ident,
                float(v["x"]),
                float(v["z"]),
                float(v.get("y", 0)),
            )
        )


# --------------------------------------------------------------------------------------------
# Tile sources
# --------------------------------------------------------------------------------------------


class Source:
    """Published service over HTTP (read-only GETs) or a local tile directory."""

    def __init__(
        self, url: str | None = None, tiles: Path | None = None, cache: Path | None = None
    ):
        self.url = url if url is None or url.endswith("/") else url + "/"
        self.dir = tiles
        self.cache = cache
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)

    @property
    def label(self) -> str:
        return str(self.dir) if self.dir else self.url

    def _get(self, url: str) -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers={"User-Agent": "turku3d-richness-scorecard"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def fragment(self, tile: str) -> dict | None:
        if self.dir:
            path = self.dir / tile / "manifest.json"
            return json.loads(path.read_text()) if path.exists() else None
        # No distance hint: a tile that is not built yet is only queued at the lowest priority.
        status, body = self._get(f"{self.url}{tile}/manifest.json")
        if status != 200:
            return None
        return json.loads(body)

    def blob(self, tile: str, name: str, sha256: str | None = None, *, named=False) -> bytes | None:
        """A tile file by its manifest URL. ``named``: the file name is its content hash (shared
        buffers), so it is cached under that name without a separate digest."""
        if named and sha256 is None:
            stem = Path(urllib.parse.urlparse(name).path).stem
            sha256 = (
                stem if len(stem) == 64 and all(c in "0123456789abcdef" for c in stem) else None
            )
        if self.cache and sha256:
            hit = self.cache / f"{sha256}{Path(name).suffix}"
            if hit.exists():
                return hit.read_bytes()
        if self.dir:
            path = (self.dir / tile / name).resolve()
            if not path.exists():
                return None
            data = path.read_bytes()
        else:
            status, data = self._get(urllib.parse.urljoin(f"{self.url}{tile}/", name))
            if status != 200:
                return None
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError(f"{tile}/{name}: sha256 mismatch")
        if self.cache and sha256:
            (self.cache / f"{sha256}{Path(name).suffix}").write_bytes(data)
        return data

    def load(self, tile: str, *, ground=False) -> TileData:
        out = TileData(tile)
        fragment = self.fragment(tile)
        if fragment is None or not fragment.get("visual", {}).get(tile):
            out.missing = "not built or not served"
            return out
        out.generation = str(fragment.get("generation") or fragment.get("code") or "")
        out.pass_kind = str(fragment.get("pass_kind") or "")
        visual = fragment["visual"][tile]
        data = self.blob(tile, visual["url"], visual.get("sha256"))
        if data is None:
            out.missing = f"visual {visual['url']} not found"
            return out
        read_visual(
            data, tile, out, ground=ground, resolve=lambda uri: self.blob(tile, uri, named=True)
        )
        roads = fragment.get("roads")
        if roads:
            graph = self.blob(tile, roads["url"], roads.get("sha256"))
            if graph:
                read_roads(graph, out)
        read_vehicles(fragment, out)
        return out


# --------------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------------


def merge(tiles: list[TileData]):
    """Seam-safe union: unique items by identity; paint and roads clipped to their own tile."""
    seen, items, paint, roads = set(), [], [], []
    for t in tiles:
        square = tile_square(t.tile)
        for it in t.items:
            key = (it.category, it.ident)
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
        if len(t.paint):
            paint.append(t.paint[in_square(t.paint[:, :2], square)])
        for flags, line in t.roads:
            roads.extend((flags, np.asarray(seg)) for seg in clip_polyline(line, square))
    return items, (np.vstack(paint) if paint else np.zeros((0, 3))), roads


def needed_tiles(camera: dict) -> list[str]:
    """Tiles touched by the 50 m disc or the street corridor (bounding boxes, padded)."""
    x, _, z = camera["pos"]
    boxes = [(x - RADIUS, z - RADIUS, x + RADIUS, z + RADIUS)]
    street = camera.get("street")
    if street:
        p = np.asarray(street["polyline"], float)
        w = street["half_width_m"]
        boxes.append((p[:, 0].min() - w, p[:, 1].min() - w, p[:, 0].max() + w, p[:, 1].max() + w))
    out = set()
    for x0, z0, x1, z1 in boxes:
        for i in range(math.floor(x0 / TILE), math.floor(x1 / TILE) + 1):
            for j in range(math.floor(z0 / TILE), math.floor(z1 / TILE) + 1):
                out.add(f"t0_{i}_{j}")
    return sorted(out)


def count_items(items: list[Item], mask_of) -> dict:
    """Counts per category (furniture also by kind) for the items the mask function selects."""
    if not items:
        return {
            "lamps": 0,
            "furniture": 0,
            "furniture_by_kind": {},
            "trees": 0,
            "parked_cars": 0,
            "sign_poles": 0,
        }
    xz = np.array([[it.x, it.z] for it in items])
    keep = mask_of(xz)
    chosen = [it for it, k in zip(items, keep, strict=True) if k]
    by = Counter(it.category for it in chosen)
    kinds = Counter(it.kind for it in chosen if it.category == "furniture")
    return {
        "lamps": by["lamp"],
        "furniture": by["furniture"],
        "furniture_by_kind": dict(sorted(kinds.items())),
        "trees": by["tree"],
        "parked_cars": by["parked_car"],
        "sign_poles": by["sign_pole"],
    }


def spawner_counts(state: dict | None, mask_of) -> dict:
    out = {}
    for key in ("moving_cars", "walkers"):
        pts = np.asarray((state or {}).get(key) or [], float).reshape(-1, 2)
        out[key] = int(mask_of(pts).sum()) if len(pts) else 0
    return out


def ground_height(tiles: list[TileData], x: float, z: float) -> float | None:
    """Highest ground-mesh surface at x/z (vertical ray against the ground triangles)."""
    best = None
    for t in tiles:
        for tri in t.ground:
            a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
            v0, v1 = (b - a)[:, [0, 2]], (c - a)[:, [0, 2]]
            v2 = np.array([x, z]) - a[:, [0, 2]]
            den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
            ok = np.abs(den) > 1e-12
            u = np.where(ok, (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / np.where(ok, den, 1), -1)
            v = np.where(ok, (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / np.where(ok, den, 1), -1)
            hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
            if hit.any():
                y = a[hit, 1] + u[hit] * (b - a)[hit, 1] + v[hit] * (c - a)[hit, 1]
                top = float(y.max())
                best = top if best is None else max(best, top)
    return best


def score_camera(
    camera: dict, tiles: list[TileData], targets: dict, spawner: dict | None = None
) -> dict:
    items, paint, roads = merge(tiles)
    x, y, z = camera["pos"]
    center = (x, z)

    def disc(p):
        return within_radius(p, center, RADIUS)

    radius = count_items(items, disc)
    radius.update(spawner_counts(spawner, disc))
    inside = disc(paint[:, :2]) if len(paint) else np.zeros(0, bool)
    radius["paint_m2"] = round(float(paint[inside, 2].sum()), 2) if len(paint) else 0.0
    radius["paint_line_m"] = round(radius["paint_m2"] / PAINT_LINE_WIDTH, 1)
    car = sum(polyline_in_circle(np.asarray(s), center) for f, s in roads if f & CAR)
    foot = sum(polyline_in_circle(np.asarray(s), center) for f, s in roads if not f & CAR)
    radius["road_car_m"], radius["road_foot_only_m"] = round(float(car), 1), round(float(foot), 1)
    result = {
        "id": camera["id"],
        "pos": camera["pos"],
        "tiles": [t.tile for t in tiles],
        "missing_tiles": {t.tile: t.missing for t in tiles if t.missing},
        "generation": sorted({t.generation for t in tiles if t.generation}),
        "pass_kind": sorted({t.pass_kind for t in tiles if t.pass_kind}),
        "radius_m": RADIUS,
        "radius": radius,
    }
    ground = ground_height(tiles, x, z)
    result["ground_y"] = None if ground is None else round(ground, 2)
    result["eye_above_ground_m"] = None if ground is None else round(y - ground, 2)
    street = camera.get("street")
    if street:
        corridor = Corridor(np.asarray(street["polyline"], float), float(street["half_width_m"]))
        length = corridor.length
        counts = count_items(items, corridor.contains)
        counts.update(spawner_counts(spawner, corridor.contains))
        in_band = corridor.contains(paint[:, :2]) if len(paint) else np.zeros(0, bool)
        paint_m2 = float(paint[in_band, 2].sum()) if len(paint) else 0.0
        counts["paint_line_m"] = paint_m2 / PAINT_LINE_WIDTH
        left_right = {
            cat: corridor.sides(
                np.array([[it.x, it.z] for it in items if it.category == key]).reshape(-1, 2)
            )
            for cat, key in (
                ("lamps", "lamp"),
                ("furniture", "furniture"),
                ("parked_cars", "parked_car"),
            )
        }
        per = {k: per_100m(v, length) for k, v in counts.items() if k != "furniture_by_kind"}
        per["furniture_by_kind"] = {
            k: per_100m(v, length) for k, v in counts["furniture_by_kind"].items()
        }
        result["per_100m"] = {
            "street": street.get("name", ""),
            "stretch_m": round(length, 1),
            "half_width_m": corridor.half_width,
            "kerb_parking": bool(street.get("kerb_parking")),
            "counts": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in counts.items()},
            "per_100m": per,
            "left_right": {k: list(v) for k, v in left_right.items()},
        }
    result["targets"] = compare(camera, result, targets)
    return result


def compare(camera: dict, result: dict, targets: dict) -> dict:
    """Status per target: ok / below / above / n/a (parked only where kerb parking exists)."""
    out = {}
    per = (result.get("per_100m") or {}).get("per_100m") or {}
    kerb = (result.get("per_100m") or {}).get("kerb_parking", False)
    for metric, table in (targets.get("per_100m") or {}).items():
        want = table.get(camera["id"])
        if want is None:
            continue
        lo, hi = (want, None) if isinstance(want, (int, float)) else (want[0], want[1])
        have = per.get(metric)
        if metric == "parked_cars" and not kerb:
            out[f"per_100m.{metric}"] = {
                "target": want,
                "value": have,
                "status": "n/a (no kerb parking)",
            }
            continue
        status = (
            "n/a"
            if have is None
            else "below"
            if have < lo
            else "above"
            if hi is not None and have > hi
            else "ok"
        )
        out[f"per_100m.{metric}"] = {"target": want, "value": have, "status": status}
    for metric, want in (targets.get("radius_min") or {}).items():
        if camera["id"] in (targets.get("radius_min_cameras") or []):
            have = result["radius"].get(metric)
            out[f"radius.{metric}"] = {
                "target": f">= {want}",
                "value": have,
                "status": "ok" if have >= want else "below",
            }
    return out


def markdown(results: list[dict], source: str) -> str:
    lines = [
        f"Source: `{source}`; generation {', '.join(sorted({g for r in results for g in r['generation']})) or '?'}.",
        "",
        f"Within {RADIUS:.0f} m of the camera:",
        "",
        "| Camera | Lamps | Furniture | Trees | Parked cars | Sign poles | Paint (line m / m²) | Car road m | Moving cars | Walkers |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        a = r["radius"]
        lines.append(
            f"| {r['id']} | {a['lamps']} | {a['furniture']} | {a['trees']} | {a['parked_cars']} | {a['sign_poles']} "
            f"| {a['paint_line_m']:.0f} / {a['paint_m2']:.1f} | {a['road_car_m']:.0f} | {a['moving_cars']} | {a['walkers']} |"
        )
    lines += [
        "",
        "Per 100 m of street, both sides (target in brackets; parked-car targets apply only where kerb parking exists):",
        "",
        "| Camera | Street (stretch m) | Lamps | Furniture | Trees | Parked cars | Sign poles | Paint line m | Moving cars | Walkers |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def cell(r, metric):
        per = r["per_100m"]["per_100m"]
        value = per.get(metric)
        text = "–" if value is None else f"{value:g}"
        t = r["targets"].get(f"per_100m.{metric}")
        if t:
            want = t["target"]
            want = f"{want[0]:g}–{want[1]:g}" if isinstance(want, list) else f"{want:g}"
            text += f" ({want}{'' if t['status'] in ('ok',) else ', ' + t['status']})"
        return text

    for r in results:
        if "per_100m" not in r:
            continue
        s = r["per_100m"]
        lines.append(
            f"| {r['id']} | {s['street']} ({s['stretch_m']:.0f}) | {cell(r, 'lamps')} | {cell(r, 'furniture')} "
            f"| {cell(r, 'trees')} | {cell(r, 'parked_cars')} | {cell(r, 'sign_poles')} | {cell(r, 'paint_line_m')} "
            f"| {cell(r, 'moving_cars')} | {cell(r, 'walkers')} |"
        )
    kinds = sorted({k for r in results for k in r["radius"]["furniture_by_kind"]})
    if kinds:
        lines += [
            "",
            "Furniture by kind within 50 m: "
            + "; ".join(
                f"{r['id']}: "
                + (
                    ", ".join(f"{k} {v}" for k, v in r["radius"]["furniture_by_kind"].items())
                    or "none"
                )
                for r in results
            )
            + ".",
        ]
    eye = [
        f"{r['id']} {r['eye_above_ground_m']:+.2f} m"
        for r in results
        if r["eye_above_ground_m"] is not None
    ]
    if eye:
        lines += ["", "Camera eye above the tile ground mesh: " + ", ".join(eye) + "."]
    missing = {t: m for r in results for t, m in r["missing_tiles"].items()}
    if missing:
        lines += [
            "",
            "Missing tiles: " + ", ".join(f"{t} ({m})" for t, m in sorted(missing.items())) + ".",
        ]
    return "\n".join(lines) + "\n"


def load_cameras(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data["cameras"] if isinstance(data, dict) else data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url", default=None, help=f"published coarse root (default {DEFAULT_URL})")
    src.add_argument("--tiles", type=Path, help="local tile directory with the service layout")
    ap.add_argument("--cameras", type=Path, default=CAMERAS)
    ap.add_argument("--targets", type=Path, default=TARGETS)
    ap.add_argument("--camera", action="append", help="only these camera ids")
    ap.add_argument("--cache", type=Path, help="content-addressed blob cache directory")
    ap.add_argument(
        "--spawner-state",
        type=Path,
        help="JSON {moving_cars: [[x,z]], walkers: [[x,z]]}, optionally keyed by camera id",
    )
    ap.add_argument("--json", type=Path, help="write the full JSON here (default stdout)")
    ap.add_argument("--markdown", type=Path, help="write the markdown tables here (default stderr)")
    ap.add_argument(
        "--ground",
        nargs=2,
        type=float,
        metavar=("X", "Z"),
        help="print the ground height at engine X, Z and exit",
    )
    a = ap.parse_args(argv)
    source = Source(url=None if a.tiles else (a.url or DEFAULT_URL), tiles=a.tiles, cache=a.cache)
    if a.ground:
        x, z = a.ground
        t = source.load(tile_of(x, z), ground=True)
        h = ground_height([t], x, z)
        print(
            json.dumps(
                {
                    "tile": t.tile,
                    "x": x,
                    "z": z,
                    "ground_y": h,
                    "eye_y": None if h is None else round(h + 1.7, 2),
                }
            )
        )
        return 0 if h is not None else 1
    targets = tomllib.loads(a.targets.read_text()) if a.targets.exists() else {}
    spawner = json.loads(a.spawner_state.read_text()) if a.spawner_state else {}
    cameras = [c for c in load_cameras(a.cameras) if not a.camera or c["id"] in a.camera]
    loaded: dict[str, TileData] = {}
    results = []
    for cam in cameras:
        names = needed_tiles(cam)
        for name in names:
            if name not in loaded:
                loaded[name] = source.load(name, ground=True)
        state = spawner.get(
            cam["id"], spawner if {"moving_cars", "walkers"} & set(spawner) else None
        )
        results.append(score_camera(cam, [loaded[n] for n in names], targets, state))
    report = {
        "source": source.label,
        "radius_m": RADIUS,
        "paint_line_width_m": PAINT_LINE_WIDTH,
        "cameras": results,
    }
    text = json.dumps(report, indent=1, default=float) + "\n"
    if a.json:
        a.json.write_text(text)
    else:
        sys.stdout.write(text)
    table = markdown(results, source.label)
    if a.markdown:
        a.markdown.write_text(table)
    else:
        sys.stderr.write(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
