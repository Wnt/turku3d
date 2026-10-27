"""Shared surface, semantic and clearance checks for instanced layer producers."""

import math
from dataclasses import dataclass, replace

import numpy as np
from shapely import STRtree, polygons, prepare
from shapely.geometry import Point

from turku3d.col.materials import mat_id
from turku3d.parked.placement import footprint


@dataclass
class Instance:
    object_id: str
    kind: str
    x: float
    y: float
    z: float
    yaw: float = 0
    width: float = 0.2
    length: float = 0.2
    height: float = 0

    @property
    def shape(self):
        return footprint(self.x, self.z, self.yaw, self.width, self.length)


class Surfaces:
    """Exact barycentric heights on published triangles, including water sensors."""

    def __init__(self, triangles, materials, decks=None):
        self.triangles = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
        self.materials = np.asarray(materials)
        self.decks = np.zeros(len(materials), bool) if decks is None else np.asarray(decks)
        self.tree = STRtree(polygons(self.triangles[:, :, [0, 2]]))

    def at(self, x, z):
        hits = []
        for i in self.tree.query(Point(x, z), predicate="intersects"):
            t = self.triangles[i]
            a, b = t[1] - t[0], t[2] - t[0]
            det = a[0] * b[2] - a[2] * b[0]
            if abs(det) < 1e-10:
                continue
            u = ((x - t[0, 0]) * b[2] - (z - t[0, 2]) * b[0]) / det
            v = (a[0] * (z - t[0, 2]) - a[2] * (x - t[0, 0])) / det
            slope = np.linalg.norm(np.cross(a, b)[[0, 2]]) / abs(det)
            if slope <= math.tan(math.radians(40)):
                hits.append(
                    (
                        float(t[0, 1] + u * a[1] + v * b[1]),
                        int(self.materials[i]),
                        bool(self.decks[i]),
                    )
                )
        return max(hits, default=None, key=lambda h: h[0])


WALK = {
    mat_id(k)
    for k in (
        "sidewalk",
        "cobblestone",
        "paving_slab",
        "gravel",
        "grass",
        "soil",
        "quay",
        "bridge_deck",
        "wood_deck",
    )
}
TREE = {mat_id("grass"), mat_id("soil")}


class Validator:
    def __init__(self, surfaces, zones=None, occupied=(), obstacles=None):
        self.surfaces = surfaces
        self.obstacles = obstacles
        self.zones = zones or {}
        self.occupied = list(occupied)
        for zone in self.zones.values():
            prepare(zone)
        road = self.zones.get("road")
        self._roads = (
            STRtree(list(road.geoms) if hasattr(road, "geoms") else [road])
            if road is not None and not road.is_empty
            else None
        )
        self._occupied_count = -1

    def intersects(self, name, shape):
        g = self.zones.get(name)
        return g is not None and not g.is_empty and g.intersects(shape)

    def facing(self, obj):
        from turku3d.props.orientation import heading

        if self._roads is None:
            return None
        p = Point(obj.x, obj.z)
        if self._roads.geometries[self._roads.nearest(p)].distance(p) > 12:
            return None
        return heading(obj.kind, obj.x, obj.z, self._roads, self.zones.get("walkway"))[0]

    def check(self, obj, check_height=True):
        shape = obj.shape
        samples = [self.surfaces.at(obj.x, obj.z)] + [
            self.surfaces.at(x, z) for x, z in list(shape.exterior.coords)[:4]
        ]
        issues = []
        if any(s is None for s in samples):
            return ["missing_support"] + (
                ["missing_bridge_deck"] if self.intersects("bridge", shape) else []
            )
        # A ground triangle labelled bridge by a 2D street polygon is NOT a deck.
        if self.intersects("bridge", shape) and not all(s[2] for s in samples):
            issues.append("missing_bridge_deck")
        if any(s[1] == mat_id("water") for s in samples) or (
            self.intersects("water", shape) and not all(s[2] for s in samples)
        ):
            issues.append("water")
        allowed = TREE if obj.kind == "tree" else WALK
        if obj.kind == "car":
            allowed = WALK | {mat_id("road")}
            parking = self.zones.get("parking")
            if not (
                (parking is not None and parking.covers(shape))
                or self.intersects("parking_edge", shape)
            ):
                issues.append("not_parking")
        if any(s[1] not in allowed for s in samples):
            if not (obj.kind == "tree" and self.intersects("tree_pit", shape)):
                issues.append("surface_type")
        if obj.kind != "car" and self.intersects("carriageway", shape):
            issues.append("carriageway")
        if obj.kind in ("sign", "post", "bus_stop", "parking_meter"):
            road = self.zones.get("road")
            if road is None or road.is_empty or road.distance(shape) > 12:
                issues.append("not_road_edge")
        for name in ("building", "crossing", "driveway"):
            if self.intersects(name, shape.buffer(0.15)):
                issues.append(name)
        if len(self.occupied) != self._occupied_count:
            self._occupied_tree = STRtree([g for _, g in self.occupied])
            self._occupied_count = len(self.occupied)
        if any(
            self.occupied[i][0] != obj.object_id
            for i in self._occupied_tree.query(shape, predicate="dwithin", distance=0.149999)
        ):
            issues.append("object_clearance")
        ys = [s[0] for s in samples]
        if max(ys) - min(ys) > 0.15:
            issues.append("uneven_support")
        if (
            obj.kind == "car"
            and self.obstacles is not None
            and self.obstacles.boxed_in(
                obj.x, obj.z, obj.yaw, obj.width, obj.length, max(ys), obj.height
            )
            is not None
        ):
            issues.append("boxed_in")
        if check_height:
            if obj.y - min(ys) > 0.15 + 1e-8:
                issues.append("floating")
            if max(ys) - obj.y > 0.02 + 1e-8:
                issues.append("buried")
        if obj.kind in (
            "bench",
            "sign",
            "bus_stop",
            "lamp",
            "bin",
            "bollard",
            "post",
            "bike_rack",
            "bike_station",
        ):
            target = self.facing(obj)
            if (
                target is not None
                and abs(math.atan2(math.sin(obj.yaw - target), math.cos(obj.yaw - target)))
                > math.pi / 4
            ):
                issues.append("orientation")
        return issues

    def repair(self, obj):
        initial = self.check(obj)
        if "missing_bridge_deck" in initial:
            return None, initial
        offsets = [(0.0, 0.0)] + sorted(
            [(i / 4, j / 4) for i in range(-8, 9) for j in range(-8, 9) if 0 < i * i + j * j <= 64],
            key=lambda p: (p[0] ** 2 + p[1] ** 2, p),
        )
        for dx, dz in offsets:
            candidate = replace(obj, x=obj.x + dx, z=obj.z + dz)
            if obj.kind in (
                "bench",
                "sign",
                "bus_stop",
                "lamp",
                "bin",
                "bollard",
                "post",
                "bike_rack",
                "bike_station",
            ):
                yaw = self.facing(candidate)
                if yaw is not None:
                    candidate.yaw = yaw
            points = [(candidate.x, candidate.z), *list(candidate.shape.exterior.coords)[:4]]
            support = [self.surfaces.at(x, z) for x, z in points]
            if any(s is None for s in support):
                continue
            candidate.y = max(s[0] for s in support)
            if not self.check(candidate):
                self.occupied.append((candidate.object_id, candidate.shape))
                return candidate, initial
        return None, initial
