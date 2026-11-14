"""Reviewed fountain centrepieces standing in their mapped basins.

A mapped basin (``garden_inputs``) draws only its rim, water and bed. A fountain whose mapped artwork
point (OSM ``artwork_type=fountain`` or ``amenity=fountain``) has a reviewed record in
``fountains.json`` also draws its centrepiece from that record's parameters: a stone pedestal on a
footing in the water, a stem, a bowl holding water, a crown and a seated figure, and stone spheres
on the basin rim's corners. Every part is a convex block of the pool's masonry; no mesh is modelled
by hand and no photograph becomes geometry. Records are keyed by the artwork's OSM identity.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely

RECORDS = Path(__file__).with_name("fountains.json")


@lru_cache(maxsize=1)
def records() -> dict:
    return json.loads(RECORDS.read_text())["fountains"]


def is_fountain(tags: dict) -> bool:
    return tags.get("artwork_type") == "fountain" or tags.get("amenity") == "fountain"


def tiles() -> frozenset:
    """Tiles drawing a reviewed centrepiece (the tile holding its artwork point)."""
    return frozenset(r["tile"] for r in records().values())


def _solid(faces, polygons):
    """Convex polygons wound outward: each face's normal points away from the solid's centre."""
    centre = np.mean([p for poly in polygons for p in poly], axis=0)
    for poly in polygons:
        p = np.asarray(poly, dtype=np.float64)
        normal = np.cross(p[1] - p[0], p[2] - p[0])
        if normal @ (p.mean(axis=0) - centre) < 0:
            p = p[::-1]
        faces.face(p)


def _prism(faces, ring, low, high):
    """A vertical convex prism over the x/z ``ring``."""
    bottom = [(x, low, z) for x, z in ring]
    top = [(x, high, z) for x, z in ring]
    sides = [[bottom[i], bottom[(i + 1) % len(ring)], top[(i + 1) % len(ring)], top[i]]
             for i in range(len(ring))]
    _solid(faces, [bottom, top, *sides])


def _frustum(faces, x, z, r0, y0, r1, y1, segments=14):
    ring0 = [(x + r0 * math.cos(a), y0, z + r0 * math.sin(a)) for a in np.linspace(0, math.tau, segments, False)]
    ring1 = [(x + r1 * math.cos(a), y1, z + r1 * math.sin(a)) for a in np.linspace(0, math.tau, segments, False)]
    sides = [[ring0[i], ring0[(i + 1) % segments], ring1[(i + 1) % segments], ring1[i]] for i in range(segments)]
    _solid(faces, [ring0, ring1, *sides])


def _sphere(faces, centre, radius, rings=5, segments=8):
    cx, cy, cz = centre
    polygons = []
    lat = np.linspace(-math.pi / 2, math.pi / 2, rings + 1)
    lon = np.linspace(0, math.tau, segments, endpoint=False)

    def point(a, b):
        return (cx + radius * math.cos(a) * math.cos(b), cy + radius * math.sin(a), cz + radius * math.cos(a) * math.sin(b))

    for i in range(rings):
        for j in range(segments):
            a0, a1, b0, b1 = lat[i], lat[i + 1], lon[j], lon[(j + 1) % segments]
            quad = [point(a0, b0), point(a0, b1), point(a1, b1), point(a1, b0)]
            # Pole rows collapse to triangles.
            unique = [p for k, p in enumerate(quad) if all(np.linalg.norm(np.subtract(p, q)) > 1e-9 for q in quad[:k])]
            if len(unique) >= 3:
                polygons.append(unique)
    _solid(faces, polygons)


def _box(faces, centre, half, axes):
    """An oriented box: ``half`` extents along the plan ``axes`` (u, v unit x/z vectors) and up."""
    cx, cy, cz = centre
    hu, hy, hv = half
    u, v = np.asarray(axes[0]), np.asarray(axes[1])
    ring = [tuple(np.array([cx, cz]) + su * hu * u + sv * hv * v) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    _prism(faces, ring, cy - hy, cy + hy)


def _figure(faces, x, z, y, spec, facing):
    """A seated animal of ``height_m`` facing ``facing`` (unit x/z): haunches, chest, head and mane,
    forelegs, as blocks."""
    h, length, width = spec["height_m"], spec["length_m"], spec["width_m"]
    f = np.asarray(facing, float)
    s = np.array([-f[1], f[0]])

    def at(forward, up, side=0.0):
        p = np.array([x, z]) + f * forward + s * side
        return (p[0], y + up, p[1])

    _box(faces, at(-0.18 * length, 0.2 * h), (0.35 * length, 0.2 * h, 0.5 * width), (f, s))   # haunches
    _box(faces, at(0.05 * length, 0.5 * h), (0.22 * length, 0.3 * h, 0.42 * width), (f, s))   # chest
    _box(faces, at(0.12 * length, 0.75 * h), (0.2 * length, 0.17 * h, 0.55 * width), (f, s))  # mane
    _box(faces, at(0.26 * length, 0.86 * h), (0.16 * length, 0.12 * h, 0.36 * width), (f, s))  # head
    for side in (-0.28, 0.28):
        _box(faces, at(0.3 * length, 0.26 * h, side * width), (0.06 * length, 0.26 * h, 0.12 * width), (f, s))


def centrepiece(stone, water, x, z, level, record, body):
    """Add a reviewed fountain's centrepiece at (x, z) in a basin of terrace ``level`` to the ``stone``
    and ``water`` faces; ``body`` is the basin polygon (its rim corners carry the spheres)."""
    bed, surface = level - 0.65, level - 0.12
    rectangle = np.asarray(body.minimum_rotated_rectangle.exterior.coords)[:3]
    u = rectangle[1] - rectangle[0]
    u = u / max(np.linalg.norm(u), 1e-9)
    v = np.array([-u[1], u[0]])
    pedestal = record["pedestal"]
    w = pedestal["width_m"] / 2
    footing = w + pedestal.get("footing_m", 0.2)
    _box(stone, (x, (bed + surface + 0.24) / 2, z), (footing, (surface + 0.24 - bed) / 2, footing), (u, v))
    top = level + pedestal["height_m"]
    _box(stone, (x, (bed + top) / 2, z), (w, (top - bed) / 2, w), (u, v))
    stem = record["stem"]
    _frustum(stone, x, z, stem["radius_m"] * 1.25, top, stem["radius_m"], top + stem["height_m"])
    bowl = record["bowl"]
    low, rim = top + stem["height_m"], top + stem["height_m"] + bowl["depth_m"]
    _frustum(stone, x, z, bowl["foot_radius_m"], low, bowl["radius_m"], rim, 20)
    # The bowl holds water just under its lip; the stone lip rings it.
    inner = bowl["radius_m"] - bowl["lip_m"]
    ring = [(x + inner * math.cos(a), z + inner * math.sin(a)) for a in np.linspace(0, math.tau, 20, False)]
    disc = [(px, rim + 0.005, pz) for px, pz in ring]
    if np.cross(np.subtract(disc[1], disc[0]), np.subtract(disc[2], disc[0]))[1] < 0:
        disc = disc[::-1]
    water.face(disc, np.asarray(ring))
    crown = record["crown"]
    _frustum(stone, x, z, crown["radius_m"], rim - 0.1, crown["radius_m"] * 0.8, rim + crown["height_m"])
    _frustum(stone, x, z, crown["radius_m"] * 1.2, rim + crown["height_m"], crown["radius_m"] * 1.2,
             rim + crown["height_m"] + 0.1)
    figure = record.get("figure")
    if figure:
        angle = math.radians(figure.get("facing_deg", 0.0))
        # Compass bearing to local x/z (x east, z south).
        facing = (math.sin(angle), -math.cos(angle))
        _figure(stone, x, z, rim + crown["height_m"] + 0.1, figure, facing)
    spheres = record.get("rim_spheres")
    if spheres:
        outer = shapely.orient_polygons(body.buffer(0.3, join_style="mitre")).simplify(0.2)
        corners = np.asarray(outer.exterior.coords)[:-1]
        for cx, cz in corners:
            # On the rim, between the water's edge and the rim's outer edge.
            p = np.array([cx, cz]) + (np.array([x, z]) - [cx, cz]) * (0.15 / max(math.dist((cx, cz), (x, z)), 1e-9))
            _sphere(stone, (p[0], level + 0.06 + spheres["radius_m"], p[1]), spheres["radius_m"])
