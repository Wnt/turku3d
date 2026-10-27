"""City-register street furniture layer with shared glTF meshes and primitive collision."""

from __future__ import annotations

import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from shapely.strtree import STRtree

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import PRIMITIVE_DTYPE, CollisionLayer, write_collision
from turku3d.package.gltf import build_glb, read_glb
from turku3d.package.manifest import _atomic_write
from turku3d.placement.inputs import context_key, place_objects
from turku3d.props.models import SUPPORTED_SIGNS, model
from turku3d.props.orientation import heading

SOURCES = {
    "GIS_Varusteet": None,
    "GIS_Liikennemerkit": "sign",
    "GIS_Follariasemat": "bike_station",
    "GIS_Polkupyoraparkki": "bike_rack",
    "GIS_Pysakointiautomaatit": "parking_meter",
    "kanta_Pysakki": "bus_stop",
    "kanta_Sahkoverkonosa": "post",
}
KINDS = (
    "bench",
    "bin",
    "bollard",
    "bike_rack",
    "bike_station",
    "parking_meter",
    "lamp",
    "bus_stop",
    "sign",
    "post",
)


@dataclass
class Prop:
    object_id: str
    kind: str
    x: float
    y: float
    z: float
    yaw: float = 0
    code: str = ""
    source: str = ""
    height_distance: float = 0
    orientation: str = "nearest_road"

    @property
    def prototype(self):
        return self.kind, self.code if self.kind == "sign" else (
            "E6" if self.kind == "bus_stop" else ""
        )


def classify(row: dict) -> str:
    label = " ".join(str(row.get(k) or "") for k in ("Tyyppi", "Varustelaji")).casefold()
    for needle, kind in [
        ("penkki", "bench"),
        ("rosk", "bin"),
        ("pollari", "bollard"),
        ("pyör", "bike_rack"),
        ("pyor", "bike_rack"),
        ("valaisin", "lamp"),
    ]:
        if needle in label:
            return kind
    return "post"


def face_road(x: float, z: float, roads: STRtree | None) -> tuple[float, str]:
    return heading("lamp", x, z, roads)


def read_sources(bbox, source_area="work-3x3"):
    import geopandas as gpd

    missing, candidates, skipped = [], [], Counter()
    root = C.raw_dir("wfs") / source_area

    def load(path):
        frame = gpd.read_parquet(path)
        if "geometry_local" not in frame:
            raise ValueError(f"{path}: expected fetch geometry_local")
        return frame

    road_path = root / "GIS_Keskilinjat.parquet"
    roads = None
    if road_path.exists():
        frame = load(road_path)
        roads = STRtree(
            [
                r.geometry_local
                for _, r in frame.iterrows()
                if r.geometry_local.geom_type == "LineString"
                and str(r.get("Katuosalaji", "")).casefold() == "ajorata"
            ]
        )
    else:
        missing.append(road_path.name)
    for source, default in SOURCES.items():
        path = root / f"{source}.parquet"
        if not path.exists():
            missing.append(path.name)
            continue
        for row in load(path).to_dict("records"):
            p = row["geometry_local"]
            if p is None or p.is_empty or p.geom_type != "Point":
                skipped["non_point"] += 1
                continue
            if not bbox.contains(p.x, p.y):
                continue
            kind = default or classify(row)
            code = str(row.get("Varustelaji_koodi") or "") if kind == "sign" else ""
            if kind == "sign" and code not in SUPPORTED_SIGNS:
                skipped["unsupported_sign:" + code] += 1
                kind = "post"
            oid = str(row.get("Id") or row["feature_key"])
            candidates.append(Prop(f"{source}:{oid}", kind, p.x, 0, p.y, code=code, source=source))
    osm_paths = sorted((C.raw_dir("osm") / source_area).glob("*.parquet"))
    if not osm_paths:
        missing.append("osm/*.parquet")
    else:
        # Dated fetch snapshot: consume the latest one once, never overlapping epochs.
        path = osm_paths[-1]
        for row in load(path).to_dict("records"):
            tags = row.get("tags", {})
            if isinstance(tags, str):
                tags = json.loads(tags)
            tags = dict(tags or {})
            kind = (
                "lamp"
                if tags.get("highway") == "street_lamp"
                else ("bollard" if tags.get("barrier") == "bollard" else None)
            )
            p = row["geometry_local"]
            if kind and p is not None and p.geom_type == "Point" and bbox.contains(p.x, p.y):
                oid = str(row.get("osm_id", row.get("id")))
                candidates.append(Prop("osm:" + oid, kind, p.x, 0, p.y, source="osm"))
    # Preserve separate signs; collapse only same-kind duplicate records at the same location.
    props, seen, locations = [], set(), defaultdict(list)
    for p in sorted(candidates, key=lambda p: (p.source == "osm", p.object_id)):
        key = (p.kind, p.code, round(p.x, 2), round(p.z, 2))
        if p.object_id in seen or key in locations["exact"]:
            skipped["duplicates"] += 1
            continue
        # Stop symbols and E6 register records often describe the same pole.
        if p.kind == "bus_stop" and any(
            math.hypot(p.x - q.x, p.z - q.z) < 2
            for q in props
            if q.kind == "sign" and q.code == "E6"
        ):
            skipped["duplicate_stop"] += 1
            continue
        seen.add(p.object_id)
        locations["exact"].append(key)
        p.yaw, p.orientation = heading(p.kind, p.x, p.z, roads)
        props.append(p)
    return props, {"missing_sources": missing, "skipped": dict(skipped)}


def primitives(p: Prop) -> np.ndarray:
    parts = []

    def add(kind, center, half=(0, 0, 0), radius=0, height=0, material=28):
        a = np.zeros((), dtype=PRIMITIVE_DTYPE)
        cx, cy, cz = center
        c, s = math.cos(p.yaw), math.sin(p.yaw)
        a["kind"], a["center"] = kind, (p.x + c * cx + s * cz, p.y + cy, p.z - s * cx + c * cz)
        a["half_extents"], a["radius"], a["height"] = half, radius, height
        a["yaw"], a["material"] = p.yaw, material
        parts.append(a)

    if p.kind == "bench":
        add("box", (0, 0.25, 0), (0.90, 0.25, 0.28))
        add("box", (0, 0.73, -0.24), (0.90, 0.17, 0.04))
    elif p.kind in ("bike_rack", "bike_station"):
        for x in (-0.9, 0, 0.9):
            for z in (-0.3, 0.3):
                add("cylinder", (x, 0.40, z), radius=0.04, height=0.8)
            add("box", (x, 0.78, 0), (0.04, 0.04, 0.34))
        if p.kind == "bike_station":
            add("box", (-1.5, 0.65, 0), (0.175, 0.65, 0.125))
    elif p.kind == "parking_meter":
        add("box", (0, 0.85, 0), (0.2, 0.85, 0.15))
    else:
        radius, height = {
            "lamp": (0.085, 6),
            "sign": (0.035, 2.7),
            "bus_stop": (0.035, 2.7),
            "bin": (0.28, 0.90),
        }.get(p.kind, (0.09, 0.95))
        add(
            "cylinder",
            (0, height / 2, 0),
            radius=radius,
            height=height,
            material=27 if p.kind == "bollard" else (28 if p.kind == "bin" else 25),
        )
    return np.array(parts, dtype=PRIMITIVE_DTYPE)


def visual_bytes(props, tile):
    """Standard glTF mesh reuse: one transformed node per registered object."""
    keys = sorted({p.prototype for p in props})
    meshes = [model(*key) for key in keys]
    js, blob = read_glb(build_glb(meshes, root_translation=tile.origin))
    js["nodes"] = [js["nodes"][0]]
    js["nodes"][0]["children"] = list(range(1, len(props) + 1))
    for p in props:
        js["nodes"].append(
            {
                "name": p.object_id,
                "mesh": keys.index(p.prototype),
                "translation": [p.x - tile.origin[0], p.y, p.z - tile.origin[2]],
                "rotation": [0, math.sin(p.yaw / 2), 0, math.cos(p.yaw / 2)],
                "extras": {
                    "layer": "props",
                    "object_id": p.object_id,
                    "material_key": f"props_{p.kind}",
                    "prop_type": p.kind,
                    **(
                        {"pole_id": f"{tile.id}:{p.object_id}", "pole_height": 2.7}
                        if p.kind in ("sign", "bus_stop")
                        else {}
                    ),
                    "sign_code": p.code,
                    "texture_uv": "normalized",
                },
            }
        )
    data = json.dumps(js, separators=(",", ":")).encode()
    data += b" " * ((-len(data)) % 4)
    return (
        struct.pack("<III", 0x46546C67, 2, 28 + len(data) + len(blob))
        + struct.pack("<II", len(data), 0x4E4F534A)
        + data
        + struct.pack("<II", len(blob), 0x004E4942)
        + blob
    )


def write_tile(props, directory, tile):
    """One L0 tile's visual and collision files; every prop lies in the tile."""
    directory = Path(directory)
    if any(C.VisualTile.at(p.x, p.z) != tile for p in props):
        raise ValueError(f"{tile.id}: props outside the tile")
    collisions = defaultdict(list)
    for p in props:
        # Sign and stop poles are session-owned Rapier bodies. Baking their primitive
        # here would leave an invisible, unbreakable collider after they topple.
        if p.kind not in ("sign", "bus_stop"):
            collisions[C.CollisionTile.at(p.x, p.z)].extend(primitives(p))
    if not props:
        return {
            "objects": 0,
            "bytes": 0,
            "triangles": 0,
            "collision_primitives": 0,
            "collision_bytes": 0,
        }
    data = visual_bytes(props, tile)
    _atomic_write(directory / f"{tile.id}.vis.glb", data)
    for cell, parts in sorted(collisions.items()):
        write_collision(
            directory / f"{cell.id}.col.npz",
            CollisionLayer(
                np.empty((0, 3), np.float64),
                np.empty((0, 3), np.uint32),
                np.empty(0, np.uint8),
                np.array(parts, dtype=PRIMITIVE_DTYPE),
            ),
        )
    return {
        "objects": len(props),
        "bytes": len(data),
        "mesh_prototypes": len({p.prototype for p in props}),
        "triangles": sum(len(model(*p.prototype).indices) for p in props),
        "collision_primitives": sum(map(len, collisions.values())),
        "collision_bytes": sum(
            (directory / f"{cell.id}.col.npz").stat().st_size for cell in collisions
        ),
    }


def combine(directory, tiles):
    """Layer-wide inventory and metrics from every tile's records; returns manifest params."""
    directory = Path(directory)
    props = [Prop(**r) for entry in tiles.values() for r in entry["records"]]
    counts = Counter(p.kind for p in props)
    visual = {tid: entry["metrics"] for tid, entry in tiles.items() if entry["metrics"]["objects"]}
    metrics = {
        "counts": {k: counts[k] for k in KINDS},
        "objects": len(props),
        "collision_primitives": sum(m["collision_primitives"] for m in visual.values()),
        "visual_tiles": {
            tid: {k: m[k] for k in ("objects", "bytes", "mesh_prototypes", "triangles")}
            for tid, m in visual.items()
        },
        "triangles": sum(m["triangles"] for m in visual.values()),
        "visual_bytes": sum(m["bytes"] for m in visual.values()),
        "collision_bytes": sum(m["collision_bytes"] for m in visual.values()),
        "ground_nearest_max_m": max((p.height_distance for p in props), default=0),
        "orientation_counts": dict(Counter(p.orientation for p in props)),
        "missing_sources": sorted(
            {name for e in tiles.values() for name in e["metrics"].get("missing_sources", [])}
        ),
        "skipped": dict(
            sum((Counter(e["metrics"].get("skipped", {})) for e in tiles.values()), Counter())
        ),
        "left_tile": sum(e["metrics"].get("left_tile", 0) for e in tiles.values()),
    }
    _atomic_write(
        directory / "metrics.json", (json.dumps(metrics, indent=2, sort_keys=True) + "\n").encode()
    )
    _atomic_write(
        directory / "instances.json",
        (json.dumps([asdict(p) for p in props], indent=2) + "\n").encode(),
    )
    return {
        "models": "procedural-cc0-v1",
        "height": "published-collision-triangles",
        "metrics": metrics,
        "sign_faces": sorted(SUPPORTED_SIGNS),
    }


def code_hash():
    base = Path(__file__).parents[1]
    return tile_layer.code_hash(
        Path(__file__).parent,
        base / "placement",
        base / "tile_layer.py",
        base / "layers.py",
        base / "config.py",
        base / "package/gltf.py",
    )


def tile_key(tile):
    """Every input a tile's props build reads: register sources, OSM and placement context."""
    area = C.source_area(tile)
    paths = {name: tile_layer.wfs(tile, name) for name in (*SOURCES, "GIS_Keskilinjat")}
    paths["osm"] = tile_layer.osm(tile)
    return {
        "sources": tile_layer.identities(paths),
        "placement": context_key(tile.bbox, "props", area),
    }


def build_tile(tile, staging):
    area = C.source_area(tile)
    props, metadata = read_sources(tile.bbox, area)
    if props:
        props = place_objects(props, "props", tile.bbox, area, report=f"props/{tile.id}")
    # A repair may move an object up to 2 m; one that left its tile is dropped, not handed over.
    inside = [p for p in props if tile.bbox.contains(p.x, p.z)]
    metrics = write_tile(inside, staging, tile)
    metrics.update(metadata, left_tile=len(props) - len(inside), source_area=area)
    return {"records": [asdict(p) for p in inside], "metrics": metrics}


PRODUCER = tile_layer.Producer("props", code_hash, tile_key, build_tile, combine)
