"""Immutable layer and cached semantic inputs; no lidar interpolation fallback."""

import hashlib
import json
from dataclasses import asdict

import numpy as np
import pyarrow.parquet as pq
from shapely import force_2d, from_wkb, make_valid, union_all
from shapely.geometry import box, shape

from turku3d import config as C
from turku3d.layers import layer_root, read_collision, read_manifest
from turku3d.placement import Instance, Surfaces, Validator
from turku3d.placement.clearance import ENVELOPE_PATH, Obstacles

# Instance producers run in this order. Each one clears only the inventories published before
# it, so a rebuild never invalidates an earlier producer and make refresh converges in one pass.
PLACEMENT_ORDER = ("props", "vegetation", "traffic_static")
INVENTORIES = {
    "props": ("instances.json", None),
    "vegetation": ("trees.json", "records"),
    "traffic_static": ("placements.json", "cars"),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def context(bounds, inputs, source_area="work-3x3", layer="props"):
    envelope = box(*bounds.as_tuple()).buffer(20)
    triangles, materials, decks = [], [], []
    for name in ("ground", "structures", "bridges"):
        root = layer_root() / name
        if not root.exists() and name in ("structures", "bridges"):
            continue
        colliders = [n for n in read_manifest(root)["tiles"] if n.endswith(".col.npz")]
        if colliders:  # Layers without collision (such as the road graph) cannot box a car in.
            inputs[str(root / "MANIFEST.json")] = digest(root / "MANIFEST.json")
        for filename in colliders:
            tile = C.CollisionTile.parse(filename.split(".")[0])
            if not envelope.intersects(box(*tile.bbox.as_tuple())):
                continue
            data = read_collision(root / filename)
            t = data.positions[data.indices]
            triangles.extend(t)
            materials.extend(data.material)
            decks.extend((data.material == 24) & (name in ("structures", "bridges")))
    if not triangles:
        raise ValueError("Placement requires published ground collision triangles")
    obstacles = solid_obstacles(envelope, inputs, layer) if layer == "traffic_static" else None
    zones = {
        k: []
        for k in (
            "water",
            "bridge",
            "building",
            "driveway",
            "crossing",
            "road",
            "carriageway",
            "walkway",
            "parking",
            "parking_edge",
            "tree_pit",
        )
    }

    def rows(path):
        inputs[str(path)] = digest(path)
        for row in pq.read_table(path).to_pylist():
            g = row.get("geometry_local")
            if g:
                g = make_valid(force_2d(from_wkb(g)))
                if not g.is_empty and g.intersects(envelope):
                    yield row, g

    root = C.raw_dir("wfs") / source_area
    for row, g in rows(root / "GIS_Katualueet.parquet"):
        kind = str(row.get("Katuosalaji", ""))
        if kind == "Silta":
            zones["bridge"].append(g)
        if kind == "Ajorata":
            zones["carriageway"].append(g)
        if kind in ("Jalkakäytävä", "Yhdistetty kevyen liikenteen väylä"):
            zones["walkway"].append(g)
    for row, g in rows(root / "GIS_Keskilinjat.parquet"):
        if row.get("Katuosalaji") == "Ajorata":
            zones["road"].append(g)
    for _, g in rows(root / "GIS_Rakennukset.parquet"):
        if g.geom_type in ("Polygon", "MultiPolygon"):
            zones["building"].append(g)
    footprints = layer_root() / "buildings/footprints.geojson"
    if footprints.exists():
        inputs[str(footprints)] = digest(footprints)
        for f in json.loads(footprints.read_text())["features"]:
            zones["building"].append(make_valid(shape(f["geometry"])))
    osm = sorted((C.raw_dir("osm") / source_area).glob("*.parquet"))
    if not osm:
        raise FileNotFoundError("Placement requires cached OSM access/water/bridge semantics")
    for row, g in rows(osm[-1]):
        t = row.get("tags") or {}
        t = json.loads(t) if isinstance(t, str) else dict(t)
        if t.get("natural") == "water" or t.get("waterway") == "riverbank":
            zones["water"].append(g)
        if t.get("bridge") and t["bridge"] != "no":
            zones["bridge"].append(g.buffer(5) if "Line" in g.geom_type else g)
        if t.get("building") and "Polygon" in g.geom_type:
            zones["building"].append(g)
        if t.get("service") in ("driveway", "parking_aisle") or t.get("entrance"):
            zones["driveway"].append(g.buffer(2))
        if t.get("highway") == "crossing" or t.get("footway") == "crossing":
            zones["crossing"].append(g.buffer(2))
        if (t.get("amenity") == "parking" or t.get("parking") == "street_side") and (
            t.get("parking") not in ("underground", "multi-storey", "rooftop")
        ):
            zones["parking"].append(g)
        if any(
            t.get(k) in ("lane", "street_side", "parallel")
            for k in ("parking:left", "parking:right", "parking:both", "parking:lane:both")
        ):
            zones["parking_edge"].append(g.buffer(6).difference(g.buffer(3)))
        if t.get("natural") == "tree" and t.get("tree_pit") not in (None, "no"):
            zones["tree_pit"].append(g.buffer(0.6))
    return Validator(
        Surfaces(triangles, materials, decks),
        {k: union_all(v) for k, v in zones.items()},
        placed_before(layer, inputs),
        obstacles,
    )


def placed_before(layer, inputs):
    """Bases of the instances published by producers that run before layer."""
    occupied = []
    for other in PLACEMENT_ORDER[: PLACEMENT_ORDER.index(layer)]:
        filename, key = INVENTORIES[other]
        p = layer_root() / other / filename
        if p.exists():
            inputs[str(p)] = digest(p)
            records = json.loads(p.read_text())
            for record in records[key] if key else records:
                obj = instance(record, other)
                occupied.append((obj.object_id, obj.shape))
    return occupied


def solid_obstacles(envelope, inputs, layer):
    """Obstacle colliders from every other published layer that overlaps the envelope."""
    inputs[str(ENVELOPE_PATH)] = digest(ENVELOPE_PATH)
    triangles, materials, primitives = [], [], []
    for root in sorted(p for p in layer_root().iterdir() if p.name != layer):
        if not (root / "MANIFEST.json").is_file():
            continue
        colliders = [n for n in read_manifest(root)["tiles"] if n.endswith(".col.npz")]
        if colliders:  # Layers without collision (such as the road graph) cannot box a car in.
            inputs[str(root / "MANIFEST.json")] = digest(root / "MANIFEST.json")
        for filename in colliders:
            tile = C.CollisionTile.parse(filename.split(".")[0])
            if not envelope.intersects(box(*tile.bbox.as_tuple())):
                continue
            data = read_collision(root / filename)
            triangles.append(data.positions[data.indices])
            materials.append(data.material)
            primitives.append(data.primitives)
    if not triangles:
        return Obstacles()
    return Obstacles(
        np.concatenate(triangles), np.concatenate(materials), np.concatenate(primitives)
    )


def instance(record, layer):
    r = record if isinstance(record, dict) else asdict(record)
    kind = r.get("kind", "tree" if layer == "vegetation" else "car")
    width, length = {
        "bench": (1.8, 0.56),
        "bin": (0.56, 0.56),
        "bike_rack": (1.88, 0.68),
        "bike_station": (3.6, 0.68),
        "parking_meter": (0.4, 0.3),
    }.get(kind, (0.2, 0.2))
    if kind == "tree":
        width = length = 2 * r["trunk_radius"]
    return Instance(
        r["object_id"],
        kind,
        r["x"],
        r.get("y", 0),
        r["z"],
        r.get("yaw", 0),
        r.get("width", width),
        r.get("length", length),
        r.get("height", 0),
    )


def place_objects(objects, layer, bounds, inputs, source_area="work-3x3", validator=None):
    from turku3d.placement.report import write_report

    validator = validator or context(bounds, inputs, source_area, layer)
    accepted, records = [], []
    for number, original in enumerate(sorted(objects, key=lambda o: instance(o, layer).object_id)):
        if number % 50 == 0:
            print(f"Placement {layer}: {number}/{len(objects)}", flush=True)
        before = instance(original, layer)
        after, issues = validator.repair(before)
        records.append(
            {
                "object_id": before.object_id,
                "kind": before.kind,
                "before": asdict(before),
                "after": asdict(after) if after else None,
                "violations": issues,
                "status": "dropped"
                if after is None
                else ("corrected" if before != after else "accepted"),
            }
        )
        if after is not None:
            for key in ("x", "y", "z", "yaw"):
                if isinstance(original, dict):
                    original[key] = getattr(after, key)
                elif hasattr(original, key):
                    setattr(original, key, getattr(after, key))
            accepted.append(original)
    # Recheck the complete result, not only source candidates; residual violations fail builds.
    hard = [
        {"object_id": o.object_id, "violations": validator.check(o)}
        for o in (instance(o, layer) for o in accepted)
        if validator.check(o)
    ]
    report = write_report(layer, bounds, records, hard, source_area)
    inputs["placement_report"] = digest(report)
    inputs["placement_code"] = hashlib.sha256(
        b"".join(
            p.read_bytes() for p in sorted(__import__("pathlib").Path(__file__).parent.glob("*.py"))
        )
    ).hexdigest()
    if hard:
        raise ValueError(f"{layer}: {len(hard)} hard placement violations; see {report}")
    return accepted
