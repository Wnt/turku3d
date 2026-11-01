"""City-register street furniture layer with shared glTF meshes and primitive collision."""

from __future__ import annotations

import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import shapely
import shapely.ops
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
    "frn_CityFurniture_LOD2": "lamp",
}
KINDS = (
    "bench",
    "bin",
    "waste_container",
    "ashtray",
    "table",
    "info_board",
    "info_pylon",
    "guide_post",
    "path_sign",
    "lifebuoy",
    "dispenser",
    "charger",
    "planter",
    "bollard",
    "bike_rack",
    "bike_station",
    "parking_meter",
    "lamp",
    "bus_stop",
    "sign",
    "post",
)
# Street-light register rows (frn:CityFurniture_LOD2 Tasonimi). Two registers describe most poles
# about 0.3-1.5 m apart; the better-surveyed row wins (lower rank) and the other is a duplicate.
LAMP_LAYERS = {
    "Kiilai:uv-pylväs mitattu": 0,
    "rr:js,pylväs,valaisin": 1,
    "rr:js,uv-pylväs": 2,
    "Kiilai:uv-pylväs digitoitu": 3,
}
LAMP_DUPLICATE = 1.5  # m: register lamp rows closer than this are one pole
OSM_DUPLICATE = 3.0  # m: an OSM item this close to a register item of its kind is the same object
# OSM tags of the furniture kinds the register may lack (bollards stay in the barrier layer).
OSM_FURNITURE = {
    ("amenity", "bench"): "bench",
    ("amenity", "waste_basket"): "bin",
    ("amenity", "bicycle_parking"): "bike_rack",
    ("vending", "parking_tickets"): "parking_meter",
    ("highway", "street_lamp"): "lamp",
}


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
    variant: str = ""  # model variant of a non-sign kind (bin colour, bike-rack length)
    rank: int = 0  # source quality among duplicates; lower wins

    @property
    def prototype(self):
        if self.kind == "sign":
            return self.kind, self.code
        return self.kind, "E6" if self.kind == "bus_stop" else self.variant


# GIS:Varusteet "Tyyppi Varustelaji Malli" label fragments, in order; None skips the row. Wall-mounted
# ash trays hang on facades, rescue ladders on quay walls, and toilets, shelters and play equipment
# are buildings or structures of their own: none of them is street furniture standing on the ground.
VARUSTEET = (
    ("seinätuhkakuppi", None),
    ("tuhkakuppi", "ashtray"),
    ("pelastustikas", None),
    ("pelastus", "lifebuoy"),
    ("syväsäiliö", "waste_container"),
    ("roskasäiliö", "waste_container"),
    ("molok", "waste_container"),
    ("deepload", "waste_container"),
    ("rosk", "bin"),
    ("kakkapussi", "dispenser"),
    ("latauspiste", "charger"),
    ("pyloni", "info_pylon"),
    ("viitta", "guide_post"),
    ("talvikunnossapito", "path_sign"),
    ("talvihoito", "path_sign"),
    ("opaste", "info_board"),
    ("opastaulu", "info_board"),
    ("nimikyltti", "info_board"),
    ("kalusteryhm", "table"),
    ("pöytä", "table"),
    ("poyta", "table"),
    ("penkki", "bench"),
    ("sohva", "bench"),
    ("tuoli", "bench"),
    ("istutus", "planter"),
    ("kukka", "planter"),
    ("amme", "planter"),
    ("pollari", "bollard"),
    ("pyör", "bike_rack"),
    ("pyor", "bike_rack"),
    ("wc", None),
    ("katos", None),
    ("leikki", None),
    ("keinu", None),
    ("peli", None),
)
BIN_COLOURS = (("vihreä", "green"), ("musta", "black"))


def classify(row: dict) -> str | None:
    """Street-furniture kind of a GIS:Varusteet row, or None for rows that are not drawn."""
    label = " ".join(str(row.get(k) or "") for k in ("Tyyppi", "Varustelaji", "Malli")).casefold()
    for needle, kind in VARUSTEET:
        if needle in label:
            return kind
    return None


def variant(kind: str, row: dict) -> str:
    """Model variant from register attributes: bin colours and bike-rack lengths."""
    model_name = str(row.get("Malli") or "").casefold()
    if kind == "bin":
        return next((v for needle, v in BIN_COLOURS if needle in model_name), "")
    if kind == "bike_rack":
        try:
            places = int(float(row.get("Pyorapaikkojen_lukumaara") or 0))
        except ValueError:
            places = 0
        # One stand holds two bikes; the shared model has 3 stands.
        return str(min(8, max(2, (places + 1) // 2))) if places else ""
    return ""


def face_road(x: float, z: float, roads: STRtree | None) -> tuple[float, str]:
    return heading("lamp", x, z, roads)


# OSM sign nodes: highway=give_way/stop, and traffic_sign=FI:<code>[;FI:<code>...].
SIGN_HIGHWAYS = {"give_way": "B5", "stop": "B6"}
SIGN_REGISTER_REACH = 25.0  # a register sign of the same code this close already marks it
ROADSIDE_OFFSET = 0.7  # a node on the carriageway moves this far past its nearest edge
# Lit streets without lamps in any source get lamps by a spacing rule, on their right side.
LIT_STREETS = {
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "pedestrian",
    "service",
}
LAMP_SPACING = 30.0
LAMP_REACH = 20.0  # a station with any lamp this close is lit already


def sign_codes(tags: dict) -> list[str]:
    codes = [SIGN_HIGHWAYS[tags["highway"]]] if tags.get("highway") in SIGN_HIGHWAYS else []
    for value in str(tags.get("traffic_sign", "")).replace(",", ";").split(";"):
        code = value.strip()
        if code.casefold().startswith("fi:"):
            code = code[3:]
        if code and code not in codes:
            codes.append(code)
    return codes


def roadside(x, z, carriageway):
    """A point on the carriageway moved just past its nearest edge (OSM maps signs on the way)."""
    point = shapely.Point(x, z)
    if carriageway.is_empty or not carriageway.covers(point):
        return x, z
    edge = shapely.ops.nearest_points(carriageway.boundary, point)[0]
    dx, dz = edge.x - x, edge.y - z
    length = math.hypot(dx, dz) or 1.0
    return edge.x + dx / length * ROADSIDE_OFFSET, edge.y + dz / length * ROADSIDE_OFFSET


def _near(tree, x, z, distance):
    return tree is not None and len(
        tree.query(shapely.Point(x, z), predicate="dwithin", distance=distance)
    )


def osm_props(frame, bbox, register, carriageway, skipped):
    """OSM furniture, lamps and signs the register lacks, plus spacing-rule lamps along lit streets.

    An OSM item of a register kind is dropped when a register item of that kind lies within
    OSM_DUPLICATE. Bollards are drawn by the barrier layer, never as props. A lit street gets a
    rule lamp only at stations with no register or OSM lamp within LAMP_REACH (per station)."""
    result, lit = [], []
    reach = bbox.buffered(LAMP_REACH)
    by_kind = defaultdict(list)
    for q in register:
        by_kind[q.kind].append(shapely.Point(q.x, q.z))
    registered = {kind: STRtree(points) for kind, points in by_kind.items()}
    for index, row in enumerate(frame.to_dict("records")):
        tags = row.get("tags", {})
        tags = dict(json.loads(tags) if isinstance(tags, str) else tags or {})
        g = row["geometry_local"]
        if g is None or g.is_empty:
            continue
        oid = f"osm:{row.get('osm_id', index)}"
        if g.geom_type == "LineString":
            if tags.get("lit") == "yes" and tags.get("highway") in LIT_STREETS:
                lit.append((oid, g, tags))
            continue
        if g.geom_type != "Point" or not reach.contains(g.x, g.y):
            continue
        for (key, value), kind in OSM_FURNITURE.items():
            if tags.get(key) != value:
                continue
            if _near(registered.get(kind), g.x, g.y, OSM_DUPLICATE):
                skipped[f"osm_{kind}_in_register"] += 1
                continue
            result.append(Prop(oid, kind, g.x, 0, g.y, source="osm", rank=10))
        for code in sign_codes(tags):
            if any(
                q.kind == "sign"
                and q.code == code
                and math.hypot(q.x - g.x, q.z - g.y) < SIGN_REGISTER_REACH
                for q in register
            ):
                skipped["osm_sign_in_register"] += 1
                continue
            x, z = roadside(g.x, g.y, carriageway)
            supported = code in SUPPORTED_SIGNS
            if not supported:
                skipped["unsupported_sign:" + code] += 1
            result.append(
                Prop(
                    f"{oid}:{code}",
                    "sign" if supported else "post",
                    x,
                    0,
                    z,
                    code=code if supported else "",
                    source="osm",
                )
            )
    lamps = [(p.x, p.z) for p in [*register, *result] if p.kind == "lamp"]
    tree = STRtree([shapely.Point(xz) for xz in lamps]) if lamps else None
    for oid, line, tags in lit:
        try:
            half = float(tags.get("width", float(tags.get("lanes", 2)) * 3)) / 2
        except ValueError:
            half = 3.0
        # Engine z points south, so a positive (left in x/y) offset is right of the way.
        side = line.offset_curve(half + 1.0)
        if side.is_empty or side.geom_type != "LineString":
            continue
        for n, distance in enumerate(np.arange(LAMP_SPACING / 2, side.length, LAMP_SPACING)):
            station = side.interpolate(float(distance))
            if not bbox.contains(station.x, station.y):
                continue
            if _near(tree, station.x, station.y, LAMP_REACH):
                skipped["lit_station_has_lamp"] += 1
                continue
            result.append(
                Prop(
                    f"{oid}:lamp:{n}",
                    "lamp",
                    station.x,
                    0,
                    station.y,
                    source="osm_lit_street",
                    rank=20,
                )
            )
    return [p for p in result if bbox.contains(p.x, p.z)]


def register_row(source, default, row, skipped):
    """(kind, code, variant, rank) of one register row; kind None when the row is not drawn."""
    kind = default or classify(row)
    if kind is None:
        label = row.get("Tyyppi") or row.get("Varustelaji")
        skipped[f"varusteet_skipped:{label}"] += 1
        return None, "", "", 0
    code = str(row.get("Varustelaji_koodi") or "") if kind == "sign" else ""
    if kind == "sign" and code not in SUPPORTED_SIGNS:
        skipped["unsupported_sign:" + code] += 1
        kind = "post"
    rank = LAMP_LAYERS.get(str(row.get("Tasonimi") or ""), 5) if kind == "lamp" else 0
    return kind, code, variant(kind, row), rank


def deduplicate_lamps(lamps, skipped):
    """One pole per LAMP_DUPLICATE disc: the better-surveyed register row (lower rank) wins."""
    kept, cells = [], defaultdict(list)
    for p in sorted(lamps, key=lambda p: (p.rank, p.object_id)):
        i, j = int(p.x // LAMP_DUPLICATE), int(p.z // LAMP_DUPLICATE)
        near = (q for di in (-1, 0, 1) for dj in (-1, 0, 1) for q in cells[(i + di, j + dj)])
        if any(math.hypot(p.x - q.x, p.z - q.z) < LAMP_DUPLICATE for q in near):
            skipped["duplicate_register_lamp"] += 1
            continue
        cells[(i, j)].append(p)
        kept.append(p)
    return kept


KERB_MOVE = 2.0  # m: furniture at most this far inside a carriageway moves to just past its kerb
KERB_CLEARANCE = 0.35  # m past the carriageway edge for a moved item


def off_carriageway(props, carriageway, skipped):
    """Nothing but signs stands on a carriageway. A register point up to KERB_MOVE inside one is
    a survey offset and moves just past the nearest edge. A lamp deeper inside hangs over the
    street (variant "hung": a light without a post); other furniture deeper inside is dropped."""
    if carriageway.is_empty:
        return props
    boundary = carriageway.boundary
    kept = []
    for p in props:
        point = shapely.Point(p.x, p.z)
        if p.kind in ("sign", "bus_stop", "post") or not carriageway.covers(point):
            kept.append(p)
            continue
        edge = shapely.ops.nearest_points(boundary, point)[0]
        depth = edge.distance(point)
        # Spacing-rule lamps are synthetic: they always belong at the kerb.
        if depth <= KERB_MOVE or p.source == "osm_lit_street":
            dx, dz = edge.x - p.x, edge.y - p.z
            length = math.hypot(dx, dz) or 1.0
            p.x = edge.x + dx / length * KERB_CLEARANCE
            p.z = edge.y + dz / length * KERB_CLEARANCE
            skipped[f"moved_off_carriageway:{p.kind}"] += 1
            kept.append(p)
        elif p.kind == "lamp":
            p.variant = "hung"
            skipped["lamp_hung_over_carriageway"] += 1
            kept.append(p)
        else:
            skipped[f"dropped_on_carriageway:{p.kind}"] += 1
    return kept


def read_sources(bbox, source_area="work-3x3", *, source_paths=None, osm_path=None):
    from turku3d.spatial import frame as spatial_frame

    missing, candidates, skipped = [], [], Counter()
    root = C.raw_dir("wfs") / source_area
    outer = bbox.buffered(max(SIGN_REGISTER_REACH, LAMP_REACH))

    def load(path, bounds=outer):
        frame = spatial_frame(path, bounds)
        if "geometry_local" not in frame:
            raise ValueError(f"{path}: expected fetch geometry_local")
        return frame

    road_path = (
        (source_paths or {}).get("GIS_Keskilinjat")
        if source_paths is not None
        else root / "GIS_Keskilinjat.parquet"
    )
    roads = None
    if road_path is not None and road_path.exists():
        frame = load(road_path, None)
        roads = STRtree(
            frame.geometry_local[
                (frame.geometry_local.geom_type == "LineString")
                & (frame["Katuosalaji"].astype(str).str.casefold() == "ajorata")
            ].values
        )
    else:
        missing.append("GIS_Keskilinjat.parquet")
    lamps = []
    for source, default in SOURCES.items():
        path = source_paths.get(source) if source_paths is not None else root / f"{source}.parquet"
        if path is None or not path.exists():
            if source_paths is None:
                missing.append(f"{source}.parquet")
            continue
        for row in load(path).to_dict("records"):
            p = row["geometry_local"]
            if p is None or p.is_empty or p.geom_type != "Point":
                skipped["non_point"] += 1
                continue
            # Register objects just past the edge still mark signs and lamps as present.
            if not outer.contains(p.x, p.y):
                continue
            kind, code, model_variant, rank = register_row(source, default, row, skipped)
            if kind is None:
                continue
            # Separate register objects can share an Id; the fetch's feature key never repeats.
            prop = Prop(
                f"{source}:{row['feature_key']}",
                kind,
                p.x,
                0,
                p.y,
                code=code,
                source=source,
                variant=model_variant,
                rank=rank,
            )
            (lamps if kind == "lamp" else candidates).append(prop)
    candidates += deduplicate_lamps(lamps, skipped)
    streets = (
        (source_paths or {}).get("GIS_Katualueet")
        if source_paths is not None
        else root / "GIS_Katualueet.parquet"
    )
    # Carriageway areas near the tile (city polygons can be invalid: repair before union).
    window = shapely.box(*outer.as_tuple())
    carriageway = (
        shapely.union_all(
            [
                shapely.make_valid(shapely.force_2d(r["geometry_local"]))
                for r in load(streets).to_dict("records")
                if r["Katuosalaji"] == "Ajorata"
                and r["geometry_local"] is not None
                and r["geometry_local"].intersects(window)
            ]
        )
        if streets is not None and streets.exists()
        else shapely.Polygon()
    )
    osm_paths = (
        ([osm_path] if osm_path else [])
        if source_paths is not None
        else sorted((C.raw_dir("osm") / source_area).glob("*.parquet"))
    )
    if not osm_paths:
        missing.append("osm/*.parquet")
    else:
        # Dated fetch snapshot: consume the latest one once, never overlapping epochs.
        candidates += osm_props(load(osm_paths[-1]), bbox, candidates, carriageway, skipped)
    candidates = [p for p in candidates if bbox.contains(p.x, p.z)]
    candidates = off_carriageway(candidates, carriageway, skipped)
    # Preserve separate signs; collapse only same-kind duplicate records at the same location.
    props, seen, locations = [], set(), set()
    for p in sorted(candidates, key=lambda p: (p.source == "osm", p.object_id)):
        key = (p.kind, p.code, round(p.x, 2), round(p.z, 2))
        if p.object_id in seen or key in locations:
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
        locations.add(key)
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
                        {
                            "pole_id": f"{tile.id}:{p.object_id}",
                            "pole_height": 2.7,
                            # World base of the pole: the client reads it instead of searching
                            # the batched index range for the lowest vertices.
                            "pole_base": [round(p.x, 3), round(p.y, 3), round(p.z, 3)],
                        }
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
