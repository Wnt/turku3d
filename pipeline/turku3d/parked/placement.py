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


class Masks:
    """Exclusion geometry kept as an STRtree of its parts: intersection tests without a union."""

    def __init__(self, parts):
        import shapely

        self.parts = [p for p in parts if p is not None and not p.is_empty]
        self.tree = shapely.STRtree(self.parts)

    def intersects(self, shape):
        return len(self.tree.query(shape, predicate="intersects")) > 0

    def contains_point(self, x, z):
        return len(self.tree.query(Point(x, z), predicate="within")) > 0


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


def static_masks(features, spawn, city, *, path=1.5, entrance=4.0, driveway=0.15, crossings=True):
    """Everything but street corridors: buildings, water, entrances, crossings, paths, pavements."""
    masks = [Point(*spawn).buffer(12)]  # Includes driven-car spawn six metres to the right.
    for f in features:
        t, g = f.tags, f.geometry
        if t.get("building") or t.get("natural") == "water" or t.get("waterway") == "riverbank":
            masks.append(g.buffer(0.5))
        if t.get("entrance") or t.get("amenity") == "parking_entrance":
            masks.append(g.buffer(4 if t.get("amenity") == "parking_entrance" else entrance))
        if crossings and (
            t.get("highway") == "crossing" or t.get("footway") == "crossing" or t.get("crossing")
        ):
            masks.append(g.buffer(5))
        h = t.get("highway")
        if h in ("footway", "path", "steps", "cycleway", "pedestrian") and g.geom_type in (
            "LineString",
            "MultiLineString",
        ):
            masks.append(g.buffer(path))
    for f in city:
        code = f.tags.get("Katuosalaji_koodi")
        if code in ("2", "3", "4", "7", "8", "16"):
            masks.append(f.geometry.buffer(driveway if code == "7" else 0.15))
    return masks


def road_masks(features, service_ends=True):
    """(corridor, feature) of every street and the 5 m discs at its ends (without the ends of
    service ways when ``service_ends`` is false: parking aisles end inside their lots)."""
    roads, ends = [], []
    for f in features:
        t, g = f.tags, f.geometry
        h = t.get("highway")
        if not h or g.geom_type not in ("LineString", "MultiLineString"):
            continue
        if h in ("footway", "path", "steps", "cycleway", "pedestrian"):
            continue
        width = 3.5 if h == "service" else road_width(t)
        roads.append((g.buffer(width / 2 + 0.35), f))
        if h == "service" and not service_ends:
            continue
        for line in getattr(g, "geoms", [g]):
            ends.extend(Point(p).buffer(5) for p in [line.coords[0], line.coords[-1]])
    return roads, ends


def exclusions(features, spawn, city):
    roads, ends = road_masks(features)
    masks = static_masks(features, spawn, city) + [r for r, _ in roads] + ends
    return unary_union(masks), unary_union([f.geometry for _, f in roads])


# Depth of a strip bay from its kerb, and the gap left to the kerb face, per orientation.
STRIP_DEPTH = {"parallel": 2.0, "perpendicular": 5.0, "diagonal": 4.9}
STRIP_KERB_GAP = 0.25
# Pitch along a kerbside strip and the bay's turn from the strip axis, per orientation.
STRIP_BAYS = {
    "parallel": (6.0, math.pi / 2),
    "perpendicular": (2.6, 0.0),
    "diagonal": (3.6, math.pi / 4),
}


AISLE_OFFSET = 5.4  # bay centre from the aisle centreline: half a 6 m aisle plus half a bay
AISLE_PITCH = 2.6


def aisle_candidates(feature, ways):
    """Perpendicular bays on both sides of every service way that runs through a lot: mapped
    aisles show how the lot is laid out better than any grid."""
    g = feature.geometry
    for way in sorted(ways, key=lambda w: w.id):
        inside = way.geometry.intersection(g)
        for part_id, line in enumerate(getattr(inside, "geoms", [inside])):
            if line.geom_type != "LineString" or line.length < 2 * AISLE_PITCH:
                continue
            for k, t in enumerate(np.arange(AISLE_PITCH / 2, line.length, AISLE_PITCH)):
                a = line.interpolate(max(0.0, t - 0.5))
                b = line.interpolate(min(line.length, t + 0.5))
                dx, dz = b.x - a.x, b.y - a.y
                n = math.hypot(dx, dz) or 1.0
                p = line.interpolate(t)
                for side in (1, -1):
                    nx, nz = -dz / n * side, dx / n * side  # away from the aisle
                    yield (
                        f"{feature.id}:aisle:{way.id}:{part_id}:{side}:{k}",
                        p.x + nx * AISLE_OFFSET,
                        p.y + nz * AISLE_OFFSET,
                        math.atan2(nx, nz),
                        g,
                    )


def long_axis(part):
    """Direction of a parking area's bay rows: its dominant edge direction (edges weighted by
    length, modulo 90 degrees), turned to whichever of the two axes the area is longer along."""
    c = np.asarray(part.exterior.coords)
    d = np.diff(c, axis=0)
    w = np.linalg.norm(d, axis=1)
    a = np.arctan2(d[:, 1], d[:, 0])
    theta = math.atan2(float((w * np.sin(4 * a)).sum()), float((w * np.cos(4 * a)).sum())) / 4
    spans = []
    for angle in (theta, theta + math.pi / 2):
        along = c @ np.array([math.cos(angle), math.sin(angle)])
        spans.append(along.max() - along.min())
    return theta if spans[0] >= spans[1] else theta + math.pi / 2


def capacity_of(tags):
    """The mapped number of bays, or None."""
    try:
        capacity = int(str(tags.get("capacity", "")).strip())
    except ValueError:
        return None
    return capacity if capacity > 0 else None


def street_side_strip(feature, part=None):
    """A separately mapped kerbside strip: its cars stand parallel and may overhang its outline."""
    g = part if part is not None else feature.geometry
    if feature.tags.get("parking") in ("lane", "street_side", "on_kerb", "half_on_kerb"):
        return True
    rect = np.asarray(g.minimum_rotated_rectangle.exterior.coords)
    sides = np.linalg.norm(np.diff(rect, axis=0), axis=1)
    return sides.min() < 7


def candidates(feature, street=None):
    """(key, x, z, yaw, area) of every bay of a parking polygon, or of a street's parallel lanes.

    ``street``, a point on the street a kerbside strip lines, puts the strip's row against its
    far (pavement) edge, 25 cm off the kerb face, the way kerb bays stand; without it the row
    follows the strip's midline."""
    g, t = feature.geometry, feature.tags
    if (
        t.get("parking") in ("underground", "multi-storey", "rooftop")
        or t.get("location") == "underground"
    ):
        return
    if g.geom_type in ("Polygon", "MultiPolygon"):
        if t.get("amenity") != "parking" and t.get("parking") != "street_side":
            return
        orientation = t.get("orientation", t.get("parking:orientation"))
        for part_id, part in enumerate(getattr(g, "geoms", [g])):
            angle = long_axis(part)
            local = affinity.rotate(part, -angle, origin=(0, 0), use_radians=True)
            xmin, zmin, xmax, zmax = local.bounds
            if street_side_strip(feature, part):
                # One row along the strip's midline: parallel unless its bays are tagged otherwise.
                kind = orientation if orientation in ("perpendicular", "diagonal") else "parallel"
                if orientation is None and zmax - zmin >= 4.4:
                    kind = "perpendicular"  # too deep for one parallel row
                pitch, turn = STRIP_BAYS[kind]
                capacity = capacity_of(t)
                if capacity:
                    # A mapped bay count spreads over the strip, down to the tightest legal bay.
                    pitch = max(pitch * 0.9, (xmax - xmin) / capacity)
                row = (zmin + zmax) / 2
                if street is not None:
                    toward = affinity.rotate(Point(street), -angle, origin=(0, 0), use_radians=True)
                    reach = STRIP_KERB_GAP + STRIP_DEPTH[kind] / 2
                    row = zmax - reach if toward.y < row else zmin + reach
                rows, cols = [row], np.arange(xmin + pitch / 2, xmax, pitch)
            else:
                # Lots: rows of perpendicular bays with 6 m access aisles.
                turn = 0.0
                rows, cols = np.arange(zmin + 2.7, zmax, 11.4), np.arange(xmin + 1.3, xmax, 2.65)
            for row, z in enumerate(rows):
                for col, x in enumerate(cols):
                    p = affinity.rotate(Point(x, z), angle, origin=(0, 0), use_radians=True)
                    yield f"{feature.id}:{part_id}:{row}:{col}", p.x, p.y, turn - angle, part
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
