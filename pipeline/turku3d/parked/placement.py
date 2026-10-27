"""Parking candidates and conservative access exclusions in engine X/Z metres."""

import hashlib
import math
from dataclasses import dataclass

import numpy as np
from shapely import affinity
from shapely.geometry import Point, box
from shapely.ops import unary_union

from .models import COLORS, MODELS


@dataclass
class Feature:
    id: str
    geometry: object
    tags: dict


def random_for(seed, key):
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "little"))


def footprint(x, z, yaw, width, length):
    # A +Y rotation maps model +Z to (sin(yaw), cos(yaw)).
    return affinity.translate(
        affinity.rotate(
            box(-width / 2, -length / 2, width / 2, length / 2), -yaw, use_radians=True
        ),
        x,
        z,
    )


def road_width(tags):
    try:
        return float(tags.get("width", str(float(tags.get("lanes", 2)) * 3)))
    except ValueError:
        return 6.0


def exclusions(features, spawn, city):
    masks = [Point(*spawn).buffer(12)]  # Includes driven-car spawn six metres to the right.
    roads = []
    for f in features:
        t, g = f.tags, f.geometry
        if t.get("building") or t.get("natural") == "water" or t.get("waterway") == "riverbank":
            masks.append(g.buffer(0.5))
        if t.get("entrance") or t.get("amenity") == "parking_entrance":
            masks.append(g.buffer(4))
        if t.get("highway") == "crossing" or t.get("footway") == "crossing" or t.get("crossing"):
            masks.append(g.buffer(5))
        h = t.get("highway")
        if h and g.geom_type in ("LineString", "MultiLineString"):
            if h in ("footway", "path", "steps", "cycleway", "pedestrian"):
                masks.append(g.buffer(1.5))
            else:
                width = 3.5 if h == "service" else road_width(t)
                masks.append(g.buffer(width / 2 + 0.35))
                roads.append(g)
                for line in getattr(g, "geoms", [g]):
                    masks.extend(Point(p).buffer(5) for p in [line.coords[0], line.coords[-1]])
    for f in city:
        if f.tags.get("Katuosalaji_koodi") in ("2", "3", "4", "7", "8", "16"):
            masks.append(f.geometry.buffer(0.15))
    return unary_union(masks), unary_union(roads)


def candidates(feature):
    g, t = feature.geometry, feature.tags
    if (
        t.get("parking") in ("underground", "multi-storey", "rooftop")
        or t.get("location") == "underground"
    ):
        return
    if g.geom_type in ("Polygon", "MultiPolygon"):
        if t.get("amenity") != "parking" and t.get("parking") != "street_side":
            return
        for part_id, part in enumerate(getattr(g, "geoms", [g])):
            rect = np.asarray(part.minimum_rotated_rectangle.exterior.coords)
            edges = np.diff(rect, axis=0)
            edge = edges[np.argmax(np.linalg.norm(edges, axis=1))]
            angle = math.atan2(edge[1], edge[0])
            local = affinity.rotate(part, -angle, origin=(0, 0), use_radians=True)
            xmin, zmin, xmax, zmax = local.bounds
            # Narrow street-side strips get parallel bays; lots retain 6 m access aisles.
            narrow = zmax - zmin < 7
            for row, z in enumerate(
                np.arange(zmin + (1.2 if narrow else 2.7), zmax, 2.6 if narrow else 11.4)
            ):
                for col, x in enumerate(
                    np.arange(xmin + (2.8 if narrow else 1.3), xmax, 6.2 if narrow else 2.65)
                ):
                    p = affinity.rotate(Point(x, z), angle, origin=(0, 0), use_radians=True)
                    yaw = math.pi / 2 - angle if narrow else -angle
                    yield f"{feature.id}:{part_id}:{row}:{col}", p.x, p.y, yaw, part
    elif g.geom_type == "LineString":
        for side, sign in [("left", 1), ("right", -1)]:
            value = t.get(
                f"parking:{side}",
                t.get(
                    "parking:both", t.get(f"parking:lane:{side}", t.get("parking:lane:both", ""))
                ),
            )
            orientation = t.get(
                f"parking:{side}:orientation", t.get("parking:both:orientation", value)
            )
            if value not in ("lane", "street_side", "parallel") or orientation not in (
                "lane",
                "street_side",
                "parallel",
            ):
                continue  # Perpendicular/diagonal streets require explicit bay polygons.
            offset = g.offset_curve(sign * (road_width(t) / 2 + 1.5))
            if offset.geom_type != "LineString":
                continue
            for n, distance in enumerate(np.arange(7, offset.length - 7, 6.4)):
                a, b = offset.interpolate(distance - 0.5), offset.interpolate(distance + 0.5)
                p = offset.interpolate(distance)
                yield f"{feature.id}:{side}:{n}", p.x, p.y, math.atan2(b.x - a.x, b.y - a.y), None


def place(features, city, bounds, spawn, seed=16):
    excluded, roads = exclusions(features, spawn, city)
    accepted, occupied, rejected = [], [], {}
    for f in sorted(features, key=lambda f: f.id):
        for key, x, z, yaw, area in candidates(f):
            rng = random_for(seed, key)
            if rng.random() > 0.8:
                continue
            name = list(MODELS)[rng.integers(len(MODELS))]
            color = list(COLORS)[rng.integers(len(COLORS))]
            width, height, length = MODELS[name]
            shape = footprint(x, z, yaw, width, length)
            reason = None
            if not bounds.covers(shape):
                reason = "aoi"
            elif area is not None and not area.buffer(-0.2).covers(shape):
                reason = "bay_boundary"
            elif excluded.intersects(shape):
                reason = "access"
            elif any(p.distance(shape) < 0.55 for p in occupied):
                reason = "spacing"
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            occupied.append(shape)
            accepted.append(
                dict(
                    object_id=key,
                    source=f.id,
                    x=x,
                    z=z,
                    yaw=yaw,
                    model=name,
                    color=color,
                    width=width,
                    height=height,
                    length=length,
                )
            )
    return accepted, excluded, roads, rejected
