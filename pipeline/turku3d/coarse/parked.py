"""Draft parking: kerbside bays and mapped lots, conservative masks and stable slot identities.

Kerb bays follow the city carriageway edges where OSM parking:*, traffic signs or permit-parking
lines allow them, or where a residential street is wide enough (inferred, low priority); see
turku3d/parked/kerbs.py. Lots and separately mapped kerbside strips come from OSM amenity=parking
polygons and from city "other traffic areas" that carry a parking sign.

Every slot draws ``occ`` (uniform 0..1) from its stable id. A car is placed when the draw is below
the slot class's peak occupancy over the day (``PEAK``); the client shows it while the draw is below
the occupancy at the shared clock's time of day (game/src/world/parkedOccupancy.ts), so all clients
show the same cars.
"""

import hashlib
import json
import math
from functools import cache

import numpy as np
import shapely

from turku3d import config as C
from turku3d.package.gltf import GlbMesh, build_glb
from turku3d.parked import kerbs
from turku3d.parked.models import COLORS, MIX, MODELS, model, palette
from turku3d.parked.placement import (
    Feature,
    Masks,
    candidates,
    aisle_candidates,
    capacity_of,
    footprint,
    random_for,
    road_masks,
    static_masks,
    street_side_strip,
)

SEED = 16
TRUCK_SIZE = (2.5, 2.5, 5.6)  # Includes trophy tyres, bumpers and roof lights.
# Turku market square: slots within CENTRE_RADIUS of it belong to the centre.
CENTRE_EN = (23459652.0, 6704624.0)
CENTRE_RADIUS = 1000.0
# Highest occupancy over the day per slot class (game/src/world/parkedOccupancy.ts has the curve).
PEAK = {
    "centre-kerb": 0.9,
    "centre-lot": 0.85,
    "residential-kerb": 0.92,
    "residential-lot": 0.9,
}
MAX_PITCH, MAX_ROLL = 0.12, 0.08  # ground grade along and across a parked car
SINK = 0.04  # m a tyre may press into ground the grid smooths (a kerb edge)


def prepare(tile, osm, streets=None, buildings=None):
    """Read complete source geometries, including neighbouring obstructions; never clip bay IDs."""
    from turku3d.spatial import rows

    window = shapely.box(*tile.bbox.buffered(30).as_tuple())
    features, city = [], []
    for path, kind in ((osm, "osm"), (streets, "city"), (buildings, "building")):
        if path is None:
            continue
        for i, row in rows(path, tile.bbox.buffered(30)):
            geom = shapely.force_2d(shapely.make_valid(row["geometry_local"]))
            if geom.is_empty or not geom.intersects(window):
                continue
            if kind == "osm":
                tags = json.loads(row["tags"]) if row.get("tags") else {}
                # Closed OSM parking ways are often encoded as lines.
                if (
                    geom.geom_type == "LineString"
                    and geom.is_ring
                    and (tags.get("amenity") == "parking" or tags.get("building"))
                ):
                    geom = shapely.Polygon(geom.coords)
                features.append(
                    Feature(f"osm:{row.get('osm_type', 'way')}:{row.get('osm_id', i)}", geom, tags)
                )
            elif kind == "city":
                city.append(Feature(f"city:{i}", geom, row))
            elif geom.area > 0.1:
                features.append(Feature(f"building:{i}", geom, {"building": "yes"}))
    return features, city


def unique(features):
    """A closed OSM way can come both as its ring and as its area: keep the first parking area."""
    seen, out = set(), []
    for f in features:
        if f.tags.get("amenity") == "parking":
            if f.id in seen:
                continue
            seen.add(f.id)
        out.append(f)
    return out


def signed_areas(city, posts, features):
    """City "other traffic areas" (Katuosalaji 12) that carry a parking sign become parking areas,
    unless OSM maps parking over most of them; E4.1 posts make their bays perpendicular, E4.3
    diagonal."""
    allow = [p for p in posts if p.allows]
    if not allow:
        return []
    mapped = shapely.union_all(
        [
            f.geometry
            for f in features
            if f.tags.get("amenity") == "parking" and f.geometry.geom_type in ("Polygon", "MultiPolygon")
        ]
    )
    points = shapely.STRtree([shapely.Point(p.x, p.z) for p in allow])
    out = []
    for f in city:
        g = f.geometry
        if f.tags.get("Katuosalaji_koodi") != "12" or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        hits = [allow[i] for i in points.query(g.buffer(3), predicate="intersects")]
        if not hits or g.intersection(mapped).area > 0.3 * g.area:
            continue
        kinds = {p.orientation for p in hits}
        tags = {"amenity": "parking", "source": "city"}
        for kind in ("perpendicular", "diagonal"):
            if kind in kinds:
                tags["orientation"] = kind
                break
        key = str(f.tags.get("feature_key") or hashlib.sha1(g.wkb).hexdigest())[:16]
        out.append(Feature(f"city:{key}", g, tags))
    return out


def zone_of(x, z, kind):
    cx, _, cz = C.en_to_local(*CENTRE_EN)
    where = "centre" if math.hypot(x - cx, z - cz) <= CENTRE_RADIUS else "residential"
    return f"{where}-{'lot' if kind == 'lot' else 'kerb'}"


def settle(tile, heights, x, z, yaw, width, length):
    """Base height, pitch and roll of a car resting on the ground under its footprint (nose up and
    leaning right are positive), or None where the ground is too steep to park on.

    The base is the least-squares plane through the ground at the corners, edge midpoints and
    centre, lifted until no sample is more than SINK below it: a single kerb or verge sample
    must neither tip the car nor leave the rest of it floating."""
    from .surfaces import grid_height

    fx, fz = math.sin(yaw), math.cos(yaw)
    rx, rz = -fz, fx  # real-world right of the heading (engine z points south)
    hl, hw = length / 2, width / 2
    a, b = (v.ravel() for v in np.meshgrid([-hl, 0, hl], [-hw, 0, hw]))
    ground = np.array(
        [grid_height(tile, heights, x + u * fx + v * rx, z + u * fz + v * rz) for u, v in zip(a, b)]
    )
    # The samples are symmetric about the centre, so the fit separates into means and slopes.
    centre = float(ground.mean())
    along = float((ground * a).sum() / (a * a).sum())
    across = -float((ground * b).sum() / (b * b).sum())  # left minus right, per metre
    if abs(along) > MAX_PITCH or abs(across) > MAX_ROLL:
        return None
    lift = float((ground - (centre + a * along - b * across)).max()) - SINK
    return centre + max(0.0, lift), math.atan(along), math.atan(across)


def truck_slot(tile, key, seed=SEED):
    return (
        int.from_bytes(
            hashlib.sha256(f"{seed}:{tile.id}:{key}:truck".encode()).digest()[:8], "little"
        )
        % 1000
        == 0
    )


def place(
    tile,
    prepared,
    heights,
    water=(),
    barriers=(),
    trees=(),
    poles=(),
    decks=(),
    seed=SEED,
    carried=None,
    stats=None,
    trace=None,
    kerb=None,
):
    """Cars for the tile's slots. ``kerb`` holds the kerb parking evidence (kerbs.prepare_inputs);
    ``stats`` counts outcomes per reason; ``trace`` collects (reason, kind, x, z, yaw) of every
    owned slot that fits no car, for audits."""

    features, city = prepared
    features = unique(features)
    kerb = kerb or {}
    posts, stops, permits = kerb.get("posts", []), kerb.get("stops", []), kerb.get("permits", [])
    stats = {} if stats is None else stats

    def reject(reason, slot=None):
        stats[reason] = stats.get(reason, 0) + 1
        if trace is not None and slot is not None:
            trace.append((reason, *slot))

    spawn = C.en_to_local(C.OFFICE_E, C.OFFICE_N)[::2]
    common = [g.buffer(1) for g in water]
    if carried is not None:
        common.append(carried)
    for f in features:
        t, g = f.tags, f.geometry
        if (
            t.get("bridge", "no") != "no"
            or t.get("tunnel", "no") not in ("no", "building_passage")
            or t.get("layer", "0") not in ("0", "")
        ):
            common.append(g.buffer(8))
    common.extend(g.buffer(0.5) for g, _tags, *_ in barriers)
    for deck in decks:
        common.append(deck.line.buffer(float(deck.way.get("width", 8)) / 2 + 3))

    from .furniture import ground_shape

    def trunks(tree, pole):
        """Tree trunks, and the poles, lamp posts and street furniture of the furniture stage by
        their own footprints, with clearance radii."""
        return [shapely.Point(t.x, t.z).buffer(tree) for t in trees] + [
            ground_shape(p).buffer(pole) for p in poles
        ]

    roads, ends = road_masks(features, service_ends=False)
    # A lot car must be able to drive out over streets and paths, past no wall, tree or pole.
    lot_trunks = trunks(0.7, 0.6)
    hard = Masks(
        common
        + lot_trunks
        + [
            f.geometry.buffer(0.5)
            for f in features
            if f.tags.get("building") and f.geometry.geom_type in ("Polygon", "MultiPolygon")
        ]
    )
    lot_excluded = Masks(
        static_masks(features, spawn, city) + [r for r, _ in roads] + ends + common + lot_trunks
    )
    # Kerb bays stand in the street itself: only paths' own width, doors and driveway mouths, and
    # the poles and trunks on the kerb itself (a car parks right beside a sign post).
    kerb_static = (
        static_masks(features, spawn, city, path=0.3, entrance=2.0, driveway=1.0, crossings=False)
        + common
        + trunks(0.45, 0.2)
    )
    window = shapely.box(*tile.bbox.buffered(15).as_tuple())
    bays, gaps = kerbs.plan(features, city, posts, stops, permits, kerb_static, window)
    kerb_excluded = Masks(kerb_static + gaps)
    corridors = [r for r, _ in roads]
    corridor_street = [f.tags.get("name") or f.id for _, f in roads]
    corridor_tree = shapely.STRtree(corridors)

    # (key, source, x, z, yaw, area, kind, street, priority) in a fixed order: areas, then kerbs.
    slots, capacity = [], {}
    areas = [f for f in features if f.geometry.geom_type in ("Polygon", "MultiPolygon")]
    aisles = [
        f
        for f in features
        if f.tags.get("highway") == "service" and f.geometry.geom_type == "LineString"
    ]
    aisle_tree = shapely.STRtree([f.geometry for f in aisles])
    kerb_streets = [s for s in kerbs.car_streets(features) if s.highway in kerbs.KERB_STREETS]
    street_tree = shapely.STRtree([s.line for s in kerb_streets])
    for f in sorted(areas + signed_areas(city, posts, features), key=lambda f: f.id):
        capacity[f.id] = capacity_of(f.tags)
        lot, street = [], None
        if street_side_strip(f):
            # A kerbside strip lines its nearest street: bays stand at its kerb and may reach that
            # street's corridor, like kerb bays.
            found = street_tree.query_nearest(f.geometry.centroid, max_distance=15, all_matches=False)
            if len(found):
                street = kerb_streets[int(found[0])]
        elif f.tags.get("amenity") == "parking":
            ways = [aisles[i] for i in aisle_tree.query(f.geometry, predicate="intersects")]
            lot = list(aisle_candidates(f, ways))
        toward = None
        if street is not None:
            near = street.line.interpolate(street.line.project(f.geometry.centroid))
            toward = (near.x, near.y)
        # Mapped aisles lay the lot out; the grid is for lots without them.
        for key, x, z, yaw, area in lot or list(candidates(f, toward)):
            kind = "strip" if street_side_strip(f, area) else "lot"
            name = street.name if street is not None and kind == "strip" else None
            slots.append((key, f.id, x, z, yaw, area, kind, name, 1, street if name else None))
    for b in sorted(bays, key=lambda b: b.key):
        slot = (b.key, f"kerb:{b.source}", b.x, b.z, b.yaw, None, "kerb", b.street, b.priority, None)
        slots.append(slot)

    bounds = shapely.box(*tile.bbox.buffered(-0.3).as_tuple())
    accepted, occupied = [], []
    occupied_grid = {}

    def cells(shape):
        x0, z0, x1, z1 = shape.bounds
        return [
            (i, j)
            for i in range(int(np.floor((x0 - 0.55) / 10)), int(np.floor((x1 + 0.55) / 10)) + 1)
            for j in range(int(np.floor((z0 - 0.55) / 10)), int(np.floor((z1 + 0.55) / 10)) + 1)
        ]

    def blocked_by_street(shape, kind, street, area):
        """A kerb bay or a kerbside strip bay may enter its own street's corridor; a strip without
        a street nearby, only the corridors it lies in."""
        for i in corridor_tree.query(shape, predicate="intersects"):
            if kind == "lot":
                return True
            if street is not None:
                if corridor_street[i] != street:
                    return True
            elif not corridors[i].intersects(area):
                return True
        return False

    names = list(MODELS)
    weights = np.asarray([MIX[name] for name in names], dtype=float)
    weights /= weights.sum()
    pending_truck = False
    seen, filled = set(), {}
    for key, source, x, z, yaw, area, kind, street, priority, lined in slots:
        if key in seen:
            continue
        seen.add(key)
        rng = random_for(seed, key)
        occ = float(rng.random())
        zone = zone_of(x, z, kind)
        if occ >= PEAK[zone]:
            reject("empty")
            continue
        if capacity.get(source) and filled.get(source, 0) >= math.ceil(capacity[source] * PEAK[zone]):
            reject("capacity")
            continue
        name = names[int(rng.choice(len(names), p=weights))]
        color = list(COLORS)[rng.integers(len(COLORS))]
        # Only owner-tile candidates draw the rare model; carry it to the next fitting bay.
        if not tile.bbox.contains(x, z):
            continue
        excluded = lot_excluded if kind == "lot" else kerb_excluded

        # A strip bay that meets the pavement or a kerbside tree where OSM and the city survey
        # disagree steps towards its street, up to a metre.
        steps = [(x, z)]
        if lined is not None:
            near = lined.line.interpolate(lined.line.project(shapely.Point(x, z)))
            d = math.hypot(near.x - x, near.y - z)
            if d > 1e-6:
                ux, uz = (near.x - x) / d, (near.y - z) / d
                steps += [(x + ux * k, z + uz * k) for k in (0.25, 0.5, 0.75, 1.0)]

        def fit(width, length):
            outcome = None
            for px, pz in steps:
                outcome = fit_at(width, length, px, pz)
                if not isinstance(outcome, str) or outcome != "access":
                    break
            return outcome

        def fit_at(width, length, x, z, yaw=yaw, area=area, kind=kind, excluded=excluded):
            shape = footprint(x, z, yaw, width, length)
            if not bounds.covers(shape):
                return "bounds"
            # Strip bays stand at the kerb and may overhang the outline on the street side.
            margin = -0.2 if kind == "lot" else 0.5 if street is not None else 0.15
            if area is not None and not area.buffer(margin).covers(shape):
                return "bay"
            if excluded.intersects(shape) or blocked_by_street(shape, kind, street, area):
                return "access"
            near = {i for cell in cells(shape) for i in occupied_grid.get(cell, ())}
            if any(occupied[i].distance(shape) < 0.55 for i in near):
                return "spacing"
            rest = settle(tile, heights, x, z, yaw, width, length)
            if rest is None:
                return "slope"
            if kind == "lot":
                # A lot car must be able to leave ahead or behind; kerb cars pull out sideways.
                exits = [
                    footprint(
                        x + d * np.sin(yaw) * 3,
                        z + d * np.cos(yaw) * 3,
                        yaw,
                        width + 0.6,
                        length + 6,
                    )
                    for d in (-1, 1)
                ]
                if all(hard.intersects(exit) for exit in exits):
                    return "boxed_in"
            return shape, rest, x, z

        width, height, length = MODELS[name]
        fitted = fit(width, length)
        if isinstance(fitted, str):
            reject(fitted, (kind, x, z, yaw))
            continue
        # Draw among viable occupied slots, so rejected buildings/roads do not inflate rarity.
        pending_truck |= truck_slot(tile, key, seed)
        is_truck = False
        if pending_truck:
            truck_fit = fit(TRUCK_SIZE[0], TRUCK_SIZE[2])
            if not isinstance(truck_fit, str):
                fitted = truck_fit
                width, height, length = TRUCK_SIZE
                is_truck = True
                pending_truck = False
        shape, (y, pitch, roll), x, z = fitted
        reject("placed")
        filled[source] = filled.get(source, 0) + 1
        accepted.append(
            dict(
                object_id=key,
                source=source,
                x=float(x),
                y=float(y),
                z=float(z),
                yaw=float(yaw),
                pitch=round(pitch, 4),
                roll=round(roll, 4),
                model="truck" if is_truck else name,
                color=color,
                width=width,
                height=height,
                length=length,
                zone=zone,
                occ=round(occ, 4),
                priority=int(priority),
                excluded=excluded,
            )
        )
        for cell in cells(shape):
            occupied_grid.setdefault(cell, []).append(len(occupied))
        occupied.append(shape)
    tree = shapely.STRtree(occupied)
    for i, car in enumerate(accepted):
        excluded = car.pop("excluded")
        for field, sign in (("clear_front_m", 1), ("clear_rear_m", -1)):
            free = 0
            for distance in range(1, 31):
                corridor = footprint(
                    car["x"] + sign * np.sin(car["yaw"]) * distance / 2,
                    car["z"] + sign * np.cos(car["yaw"]) * distance / 2,
                    car["yaw"],
                    car["width"],
                    car["length"] + distance,
                )
                if excluded.intersects(corridor) or any(
                    j != i for j in tree.query(corridor, predicate="intersects")
                ):
                    break
                free = distance
            car[field] = free
    return accepted


@cache
def model_bytes(name, color):
    pos, idx, normals, uv = model(name)
    return build_glb(
        [
            GlbMesh(
                name, pos, idx, normals=normals, uvs=uv, image=palette(name, color), roughness=0.42
            )
        ]
    )


def publish_models(cars, root):
    from .build import _entry, _write

    entries = {}
    for name, color in sorted({(c["model"], c["color"]) for c in cars if c["model"] != "truck"}):
        data = model_bytes(name, color)
        url = f"../shared/{hashlib.sha256(data).hexdigest()}.glb"
        _write(root / "shared" / url.split("/")[-1], data)
        entries[f"{name}/{color}"] = _entry(url, data)
    return entries


def stage(cache, tile, inputs, grid_pos, trees, poles, decks, carried=None):
    from pathlib import Path

    from turku3d.stage_cache import source_key

    from . import surfaces
    from .inputs import CELLS

    base = Path(__file__).parents[1]
    return cache.run(
        "parked",
        source_key(
            Path(__file__),
            base / "parked/placement.py",
            base / "parked/kerbs.py",
            base / "parked/models.py",
            base / "coarse/furniture.py",
            surfaces.grid_height,
            Path(C.__file__),
        ),
        [
            tile,
            inputs.parking,
            inputs.kerb_parking,
            grid_pos[:, 1],
            inputs.water,
            inputs.barriers,
            trees,
            poles,
            decks,
            carried,
        ],
        lambda: logged(
            tile,
            lambda stats: place(
                tile,
                inputs.parking,
                grid_pos[:, 1].reshape(CELLS + 1, CELLS + 1),
                inputs.water,
                inputs.barriers,
                trees,
                poles,
                decks,
                carried=carried,
                stats=stats,
                kerb=inputs.kerb_parking,
            ),
        ),
    )


def logged(tile, run):
    """Run a placement and log its outcome counts (the worker log keeps them for audits)."""
    import sys

    stats = {}
    cars = run(stats)
    print(f"parked {tile.id}: {json.dumps(stats, sort_keys=True)}", file=sys.stderr)
    return cars
