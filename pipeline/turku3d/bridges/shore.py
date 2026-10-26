"""Locate supported shores and make continuous bridge-to-ground approaches."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon

from turku3d.structures.geometry import Faces, planar_uv, profile_wall


@dataclass
class Shore:
    side: str
    water_s: float
    contact_s: float
    offsets: np.ndarray
    heights: np.ndarray
    lidar_error_m: float
    ground_error_m: float
    quay: dict | None
    ground_available: bool = True


def frame(axis):
    start = np.asarray(axis.coords[0], float)[:2]
    end = np.asarray(axis.coords[-1], float)[:2]
    forward = (end - start) / np.linalg.norm(end - start)
    across = np.array([-forward[1], forward[0]])
    return start, forward, across


def at(axis, station, offset=0):
    origin, forward, across = frame(axis)
    return origin + forward * station + across * offset


def span(footprint, axis):
    origin, forward, _ = frame(axis)
    polygons = list(footprint.geoms) if footprint.geom_type == "MultiPolygon" else [footprint]
    projected = [
        float((np.asarray(p, float)[:2] - origin) @ forward)
        for part in polygons
        for p in part.exterior.coords
    ]
    return min(projected), max(projected)


def strip(axis, start, end):
    _, _, across = frame(axis)
    a, b = at(axis, start), at(axis, end)
    return Polygon([a - across * 50, b - across * 50, b + across * 50, a + across * 50])


def width(footprint, axis, station):
    _, _, across = frame(axis)
    centre = at(axis, station)
    cut = footprint.intersection(LineString([centre - across * 30, centre + across * 30]))
    if cut.is_empty:
        raise ValueError("no bridge cross-section at the waterline")
    lines = list(cut.geoms) if cut.geom_type == "MultiLineString" else [cut]
    offsets = [float((np.asarray(p)[:2] - centre) @ across) for line in lines for p in line.coords]
    return min(offsets), max(offsets)


class Survey:
    def __init__(self, points, classes, water, ground=None):
        self.water = water
        self.ground = ground
        self.water_height = float(np.median(points[classes == 9, 1]))
        self.points = {cls: points[classes == cls] for cls in (2, 12)}
        self.trees = {cls: cKDTree(p[:, [0, 2]]) for cls, p in self.points.items()}

    def lidar(self, xz, reference=None):
        samples = []
        for cls in (2, 12):
            nearby = self.trees[cls].query_ball_point(xz, 1.0)
            if len(nearby) >= 4:
                h = float(np.median(self.points[cls][nearby, 1]))
                if h > self.water_height + 0.7:
                    samples.append((h, cls))
        if not samples:
            raise ValueError("no supported lidar shore elevation")
        if reference is None:
            return next((h for h, cls in samples if cls == 2), samples[0][0])
        return min(samples, key=lambda item: abs(item[0] - reference))[0]

    def support(self, xz, reference=None):
        lidar = self.lidar(xz, reference)
        if self.ground is None:
            return lidar, 0.0
        try:
            ground = self.ground.height(xz, reference=lidar)
        except ValueError:
            # The published four-tile ground stops at x=0; full Teatterisilta
            # crosses that edge. Its far abutment is measured from lidar.
            return lidar, 0.0
        return ground, abs(ground - lidar)

    def nearest_quay(self, xz):
        if self.ground is None:
            return None
        best = None
        for object_id, line, lower, upper in self.ground.quays:
            distances = np.linalg.norm(line - xz, axis=1)
            k = int(np.argmin(distances))
            if best is None or distances[k] < best["distance_m"]:
                best = {
                    "object_id": object_id,
                    "distance_m": float(distances[k]),
                    "top_m": float(upper[k]),
                    "bottom_m": float(lower[k]),
                }
        return best if best is not None and best["distance_m"] < 8 else None

    def has_ground(self, xz):
        if self.ground is None:
            return False
        try:
            self.ground.height(xz)
        except ValueError:
            return False
        return True

    def shores(self, crossing):
        axis = crossing.axis
        lo, hi = span(crossing.footprint, axis)
        stations = np.arange(lo, hi + 0.26, 0.25)
        wet = np.array([self.water.covers(Point(at(axis, s))) for s in stations])
        if not wet.any():
            raise ValueError(f"{crossing.name}: mapped bridge has no river intersection")
        first, last = (
            float(stations[np.flatnonzero(wet)[0]]),
            float(stations[np.flatnonzero(wet)[-1]]),
        )
        result = []
        for side, water_s, sign in (("start", first, -1), ("end", last, 1)):
            if not any(
                not self.water.covers(Point(at(axis, water_s + sign * d))) for d in (1, 2, 3)
            ):
                continue  # A short mapped gangway has a supported shore at only one end.
            left, right = width(crossing.footprint, axis, water_s - sign * 0.25)
            lateral = np.unique(
                np.r_[
                    np.linspace(left, right, max(3, int((right - left) / 0.75) + 1)),
                    [-4, 4] if crossing.road else [],
                ]
            )
            lateral = lateral[(lateral >= left - 1e-5) & (lateral <= right + 1e-5)]
            selected = None
            for distance in np.arange(3, 22.1, 0.5):
                contact_s = water_s + sign * float(distance)
                if self.water.covers(Point(at(axis, contact_s))):
                    continue
                try:
                    values = [self.support(at(axis, contact_s, offset)) for offset in lateral]
                except ValueError:
                    continue
                heights = np.array([v[0] for v in values])
                residual = max(v[1] for v in values)
                if residual > 0.06 or np.ptp(heights) > (1.0 if crossing.road else 0.45):
                    continue
                selected = Shore(
                    side,
                    water_s,
                    contact_s,
                    lateral,
                    heights,
                    residual,
                    0.0,
                    self.nearest_quay(at(axis, water_s)),
                    all(self.has_ground(at(axis, contact_s, offset)) for offset in lateral),
                )
                break
            if selected is None:
                raise ValueError(f"{crossing.name}: no lidar/ground-supported {side} shore contact")
            result.append(selected)
        return result, (first, last)


def approaches(crossing, shores, survey, deck_height):
    """Replace landward bridge polygons with sampled, seam-matched ramp strips."""
    meshes = []
    for shore in shores:
        s0, s1 = shore.water_s, shore.contact_s
        distances = np.linspace(s0, s1, max(3, int(abs(s1 - s0) / 0.5) + 1))
        offsets = shore.offsets
        base = np.empty((len(distances), len(offsets)), float)
        for j, u in enumerate(offsets):
            h0 = deck_height(at(crossing.axis, s0))
            target = shore.heights[j]
            for i, s in enumerate(distances):
                t = abs(s - s0) / abs(s1 - s0)
                base[i, j] = h0 * (1 - t) + target * t
                if i not in (0, len(distances) - 1) and not survey.water.covers(
                    Point(at(crossing.axis, s, u))
                ):
                    try:
                        ground, disagreement = survey.support(
                            at(crossing.axis, s, u), reference=base[i, j]
                        )
                        # Quay faces and bridge returns overlap here. The ground
                        # contact is usable only where it agrees with class 2/12.
                        if disagreement <= 0.06:
                            base[i, j] = max(base[i, j], ground + 0.02)
                    except ValueError:
                        pass
            # The last row is the measured ground contact, making the join exact.
            # Interior rows clear ground only where lidar supports that height.
        for category in (
            (("road", -4, 4), ("sidewalk", offsets[0], -4), ("sidewalk", 4, offsets[-1]))
            if crossing.road
            else (("sidewalk", offsets[0], offsets[-1]),)
        ):
            key, umin, umax = category
            cols = [j for j, u in enumerate(offsets) if umin - 1e-8 <= u <= umax + 1e-8]
            if len(cols) < 2:
                continue
            faces = Faces()
            for i in range(len(distances) - 1):
                for j, k in zip(cols[:-1], cols[1:], strict=True):
                    quad = []
                    for row, col in ((i, j), (i, k), (i + 1, k), (i + 1, j)):
                        s, u = distances[row], offsets[col]
                        p = at(crossing.axis, s, u)
                        lift = (
                            0.12 * (1 - abs(s - s0) / abs(s1 - s0))
                            if key == "sidewalk" and crossing.road
                            else 0
                        )
                        quad.append([p[0], base[row, col] + lift, p[1]])
                    if (
                        np.cross(np.asarray(quad[1]) - quad[0], np.asarray(quad[2]) - quad[0])[1]
                        < 0
                    ):
                        quad.reverse()
                    faces.face(quad, planar_uv(quad))
            material_key = (
                ("bridge_asphalt" if key == "road" else "bridge_stone")
                if crossing.road
                else ("painted_steel" if crossing.name.startswith("osm:") else "bridge_stone")
            )
            meshes.append(
                faces.mesh(
                    f"bridge:{crossing.name}:{shore.side}:{key}",
                    "bridge_deck",
                    metadata={"kind": "bridge_deck", "material_key": material_key},
                )
            )
        for edge in (0, -1):
            xz = np.array([at(crossing.axis, s, offsets[edge]) for s in distances])
            bottom = base[:, edge]
            meshes.append(
                profile_wall(
                    f"bridge:{crossing.name}:{shore.side}:rail:{edge}",
                    xz,
                    bottom,
                    bottom + 1.2,
                    thickness=0.08,
                    material="railing",
                )
            )
        shore.ground_error_m = float(np.max(np.abs(base[-1] - shore.heights)))
    return meshes
