"""Identify moored ships before ordinary house facades and roof extrusion."""

import json

import numpy as np
import shapely

from turku3d import config as C

from .garden_inputs import polygon


def prepare(tile, osm, buildings_path, water_path):
    from turku3d.spatial import rows

    from .inputs import _frame, buildings
    from .roofs import plane_height

    window = shapely.box(*tile.bbox.buffered(50).as_tuple())
    water = shapely.union_all([g for g, _ in _frame(water_path, tile.bbox.buffered(100))])
    mapped, amenities, seen = [], [], set()
    for key, row in rows(osm, tile.bbox.buffered(100)):
        tags = json.loads(row["tags"] or "{}")
        geom = shapely.force_2d(row["geometry_local"])
        shape = polygon(geom)
        if tags.get("amenity") in ("restaurant", "bar", "cafe", "pub", "nightclub"):
            amenities.append((geom, tags))
        identity = str(row.get("osm_id", key))
        if identity in seen or not 10 <= shape.area <= 5000:
            continue
        explicit = (
            tags.get("building") == "ship"
            or tags.get("man_made") in ("ship", "boat")
            or tags.get("ship") not in (None, "no")
            or tags.get("boat") == "yes"
            or tags.get("floating") == "yes"
            or tags.get("seamark:type") == "hulk"
        )
        if explicit and shape.intersects(window):
            for part in shapely.get_parts(shape):
                mapped.append(
                    dict(id="osm:" + identity, outline=part, tags=tags.copy(), buildings=[])
                )
            seen.add(identity)
    shells = buildings(buildings_path, tile, context=True) if buildings_path else []
    for building in shells:
        shape = building.footprint
        if not shape.intersects(window):
            continue
        overlaps = [shape.intersection(b["outline"]).area / shape.area for b in mapped]
        if overlaps and max(overlaps) >= 0.5:
            mapped[int(np.argmax(overlaps))]["buildings"].append(building)
            continue
        rectangle = shape.minimum_rotated_rectangle
        sides = np.linalg.norm(np.diff(np.asarray(rectangle.exterior.coords), axis=0), axis=1)
        # Strong water coverage plus an elongated low source footprint avoids piers,
        # quay-front houses and ordinary waterside buildings.
        if (
            20 <= shape.area <= 2000
            and max(sides) / max(min(sides), 0.01) >= 2
            and building.base <= 2
            and building.top <= 10
            and not water.is_empty
            and shape.intersection(water).area / shape.area >= 0.85
        ):
            mapped.append(
                dict(id="survey:" + building.key, outline=shape, tags={}, buildings=[building])
            )
    for boat in mapped:
        shape = boat["outline"]
        nearby = [
            (geom.distance(shape), tags) for geom, tags in amenities if geom.distance(shape) <= 3
        ]
        if nearby:
            boat["tags"].update(min(nearby, key=lambda row: row[0])[1])
        parts = [
            (g.intersection(shape), plane)
            for b in boat["buildings"]
            for g, plane in b.roof_parts
            if g.intersection(shape).area >= 0.5
        ]
        major = [
            (g, float(np.median(plane_height(p, np.asarray(g.exterior.coords)[:, :2]))))
            for g, p in parts
            if g.geom_type == "Polygon"
            and g.area >= 4
            and np.ptp(plane_height(p, np.asarray(g.exterior.coords)[:, :2])) <= 0.5
        ]
        # City ship floors sit at the waterline, unlike the lowest large roof/deck.
        boat["water"] = float(
            np.clip(min((b.base for b in boat["buildings"]), default=0.2), -0.5, 0.5)
        )
        boat["deck"] = float(
            np.clip(
                min((h for _, h in major), default=1.5), boat["water"] + 0.9, boat["water"] + 1.9
            )
        )
        boat["cabins"] = [(g, h) for g, h in major if h >= boat["deck"] + 1.8]
        boat["masts"] = []
        for building in boat["buildings"]:
            for g, p in building.roof_parts:
                height = float(np.max(plane_height(p, np.asarray(g.exterior.coords)[:, :2])))
                if 0.015 <= g.area <= 0.35 and boat["deck"] + 5 <= height <= boat["deck"] + 20:
                    point = g.centroid
                    if not any(point.distance(q) < 1.5 for q, _ in boat["masts"]):
                        boat["masts"].append((point, height))
        boat["building_keys"] = [b.key for b in boat["buildings"]]
        boat["source_facades"] = [b.facade_key for b in boat["buildings"]]
        boat["owner"] = C.VisualTile.at(shape.centroid.x, shape.centroid.y).id
        del boat["buildings"]
    return sorted(mapped, key=lambda boat: boat["id"])


def separate(buildings, boats):
    keys = {key for boat in boats for key in boat["building_keys"]}
    return [building for building in buildings if building.key not in keys]
