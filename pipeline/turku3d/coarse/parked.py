"""Fast draft parking: reuse mapped bays, conservative masks and stable slot identities."""

import hashlib
import json
from functools import cache

import numpy as np
import shapely

from turku3d import config as C
from turku3d.package.gltf import GlbMesh, build_glb
from turku3d.parked.models import COLORS, MODELS, model, palette
from turku3d.parked.placement import Feature, candidates, exclusions, footprint, random_for

SEED = 16
TRUCK_SIZE = (2.5, 2.5, 5.6)  # Includes trophy tyres, bumpers and roof lights.


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
):
    from .surfaces import grid_height

    features, city = prepared
    sx, _, sz = C.en_to_local(C.OFFICE_E, C.OFFICE_N)
    excluded, _ = exclusions(features, (sx, sz), city)
    masks = [excluded, *[g.buffer(1) for g in water]]
    if carried is not None:
        masks.append(carried)
    for f in features:
        t, g = f.tags, f.geometry
        if (
            t.get("bridge", "no") != "no"
            or t.get("tunnel", "no") not in ("no", "building_passage")
            or t.get("layer", "0") not in ("0", "")
        ):
            masks.append(g.buffer(8))
    masks.extend(g.buffer(0.5) for g, _tags, *_ in barriers)
    masks.extend(shapely.Point(t.x, t.z).buffer(0.7) for t in trees)
    masks.extend(shapely.Point(p.x, p.z).buffer(0.6) for p in poles)
    for deck in decks:
        masks.append(deck.line.buffer(float(deck.way.get("width", 8)) / 2 + 3))
    excluded = shapely.union_all(masks)
    shapely.prepare(excluded)
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

    pending_truck = False
    for f in sorted(features, key=lambda f: f.id):
        for key, x, z, yaw, area in candidates(f):
            rng = random_for(seed, key)
            if rng.random() > 0.8:
                continue
            name = list(MODELS)[rng.integers(len(MODELS))]
            color = list(COLORS)[rng.integers(len(COLORS))]
            # Only owner-tile candidates draw the rare model; carry it to the next fitting bay.
            if not tile.bbox.contains(x, z):
                continue

            def fit(width, length, x=x, z=z, yaw=yaw, area=area):
                shape = footprint(x, z, yaw, width, length)
                if (
                    not bounds.covers(shape)
                    or (area is not None and not area.buffer(-0.2).covers(shape))
                    or excluded.intersects(shape)
                    or any(
                        occupied[i].distance(shape) < 0.55
                        for i in {i for cell in cells(shape) for i in occupied_grid.get(cell, ())}
                    )
                ):
                    return None
                ys = [
                    grid_height(tile, heights, a, b)
                    for a, b in list(shape.exterior.coords)[:4] + [(x, z)]
                ]
                if max(ys) - min(ys) > 0.15:
                    return None
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
                if all(excluded.intersects(exit) for exit in exits):
                    return None
                return shape, ys

            width, height, length = MODELS[name]
            fitted = fit(width, length)
            if fitted is None:
                continue
            # Draw among viable occupied slots, so rejected buildings/roads do not inflate rarity.
            pending_truck |= truck_slot(tile, key, seed)
            is_truck = False
            if pending_truck:
                truck_fit = fit(TRUCK_SIZE[0], TRUCK_SIZE[2])
                if truck_fit is not None:
                    fitted = truck_fit
                    width, height, length = TRUCK_SIZE
                    is_truck = True
                    pending_truck = False
            shape, ys = fitted
            accepted.append(
                dict(
                    object_id=key,
                    source=f.id,
                    x=float(x),
                    y=float(max(ys)),
                    z=float(z),
                    yaw=float(yaw),
                    model="truck" if is_truck else name,
                    color=color,
                    width=width,
                    height=height,
                    length=length,
                )
            )
            for cell in cells(shape):
                occupied_grid.setdefault(cell, []).append(len(occupied))
            occupied.append(shape)
    tree = shapely.STRtree(occupied)
    for i, car in enumerate(accepted):
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
            base / "parked/models.py",
            Path(surfaces.__file__),
            Path(C.__file__),
        ),
        [
            tile,
            inputs.parking,
            grid_pos[:, 1],
            inputs.water,
            inputs.barriers,
            trees,
            poles,
            decks,
            carried,
        ],
        lambda: place(
            tile,
            inputs.parking,
            grid_pos[:, 1].reshape(CELLS + 1, CELLS + 1),
            inputs.water,
            inputs.barriers,
            trees,
            poles,
            decks,
            carried=carried,
        ),
    )
