"""Immutable layer and cached semantic inputs; no lidar interpolation fallback."""

import hashlib
import json
from dataclasses import asdict

import numpy as np
import pyarrow.parquet as pq
from shapely import force_2d, from_wkb, make_valid, union_all
from shapely.geometry import Point, box, shape

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


# context() reads ground triangles within this distance of the bounds; the inventories of
# earlier producers matter a little farther out (their footprints reach into the envelope).
CONTEXT_MARGIN = 20
INVENTORY_MARGIN = CONTEXT_MARGIN + 10
SURFACE_LAYERS = ("ground", "structures", "bridges")


def context_key(bounds, layer, source_area):
    """Identities of everything context() and placed_before() read for these bounds.

    Collision tiles are named by their published hashes, footprints and earlier inventories
    by the records that reach the envelope, so an unrelated change elsewhere keeps the key."""
    from turku3d.tile_layer import identities

    envelope = box(*bounds.as_tuple()).buffer(CONTEXT_MARGIN)
    cells = {}
    roots = sorted(p for p in layer_root().iterdir() if p.is_dir()) if layer_root().is_dir() else []
    for root in roots:
        surface = root.name in SURFACE_LAYERS
        if not (surface or (layer == "traffic_static" and root.name != layer)):
            continue
        manifest = root / "MANIFEST.json"
        if not manifest.is_file():
            continue
        for filename, entry in json.loads(manifest.read_text())["tiles"].items():
            if filename.endswith(".col.npz"):
                tile = C.CollisionTile.parse(filename.split(".")[0])
                if envelope.intersects(box(*tile.bbox.as_tuple())):
                    cells[f"{root.name}/{filename}"] = entry["sha256"]
    wfs = C.raw_dir("wfs") / source_area
    sources = {
        name: wfs / f"{name}.parquet"
        for name in ("GIS_Katualueet", "GIS_Keskilinjat", "GIS_Rakennukset")
    }
    osm = sorted((C.raw_dir("osm") / source_area).glob("*.parquet"))
    if osm:
        sources["osm"] = osm[-1]
    if layer == "traffic_static":
        sources["envelope"] = ENVELOPE_PATH
    reach = envelope.buffer(INVENTORY_MARGIN - CONTEXT_MARGIN)
    footprints = layer_root() / "buildings/footprints.geojson"
    nearby = (
        sorted(
            json.dumps(f, sort_keys=True)
            for f in json.loads(footprints.read_text())["features"]
            if shape(f["geometry"]).intersects(envelope)
        )
        if footprints.is_file()
        else []
    )
    before = {}
    for other in PLACEMENT_ORDER[: PLACEMENT_ORDER.index(layer)]:
        filename, key = INVENTORIES[other]
        path = layer_root() / other / filename
        if path.is_file():
            records = json.loads(path.read_text())
            before[other] = sorted(
                json.dumps(r, sort_keys=True)
                for r in (records[key] if key else records)
                if reach.contains(Point(r["x"], r["z"]))
            )
    return {
        "collision": cells,
        "sources": identities(sources),
        "footprints": hashlib.sha256("".join(nearby).encode()).hexdigest(),
        "placed_before": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
    }


def context(bounds, source_area="work-3x3", layer="props"):
    envelope = box(*bounds.as_tuple()).buffer(CONTEXT_MARGIN)
    triangles, materials, decks = [], [], []
    for name in SURFACE_LAYERS:
        root = layer_root() / name
        if not root.exists() and name in ("structures", "bridges"):
            continue
        colliders = [n for n in read_manifest(root)["tiles"] if n.endswith(".col.npz")]
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
    obstacles = solid_obstacles(envelope, layer) if layer == "traffic_static" else None
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
        placed_before(layer),
        obstacles,
    )


def placed_before(layer):
    """Bases of the instances published by producers that run before layer."""
    occupied = []
    for other in PLACEMENT_ORDER[: PLACEMENT_ORDER.index(layer)]:
        filename, key = INVENTORIES[other]
        p = layer_root() / other / filename
        if p.exists():
            records = json.loads(p.read_text())
            for record in records[key] if key else records:
                obj = instance(record, other)
                occupied.append((obj.object_id, obj.shape))
    return occupied


def solid_obstacles(envelope, layer):
    """Obstacle colliders from every other published layer that overlaps the envelope."""
    triangles, materials, primitives = [], [], []
    for root in sorted(p for p in layer_root().iterdir() if p.name != layer):
        if not (root / "MANIFEST.json").is_file():
            continue
        colliders = [n for n in read_manifest(root)["tiles"] if n.endswith(".col.npz")]
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


def place_objects(objects, layer, bounds, source_area="work-3x3", validator=None, report=None):
    """Validate and repair; `report` names the audit directory (default: the layer)."""
    from turku3d.placement.report import write_report

    validator = validator or context(bounds, source_area, layer)
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
    path = write_report(report or layer, bounds, records, hard, source_area)
    if hard:
        raise ValueError(f"{layer}: {len(hard)} hard placement violations; see {path}")
    return accepted
