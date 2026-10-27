"""Survey grouping and lidar estimates. Missing evidence is an error, never invented terrain."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString


@dataclass
class Flight:
    object_id: str
    bottom_xz: np.ndarray
    top_xz: np.ndarray
    width: float
    source_ids: list[str]


def group_flights(lines, *, gap=1.1, angle_deg=12):
    """Group parallel, overlapping nosings; keep landings and adjacent flights separate.

    Each item is (source id, LineString, code). 66618 only extends a component
    seeded by 66626; it is a mixed survey code, not independent proof of stairs.
    Bent outlines are not flattened to a misleading straight nosing.
    """
    records = []
    rejected = []
    for object_id, line, code in sorted(lines, key=lambda x: x[0]):
        p = np.asarray(line.coords)[:, :2]
        d = p[-1] - p[0]
        length = np.linalg.norm(d)
        if length < 0.3 or line.length > length * 1.03:
            if code == "66626":
                rejected.append(
                    {"object_id": object_id, "reason": "nonlinear or short stair survey line"}
                )
            continue
        d /= length
        if d[0] < 0 or (d[0] == 0 and d[1] < 0):
            d = -d
        records.append((object_id, p, d, code))
    parent = list(range(len(records)))

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    cos = np.cos(np.radians(angle_deg))
    for i, (_, a, u, _) in enumerate(records):
        v = np.array([-u[1], u[0]])
        ar = a @ u
        for j in range(i):
            _, b, ub, _ = records[j]
            if abs(u @ ub) < cos:
                continue
            br = b @ u
            overlap = min(ar.max(), br.max()) - max(ar.min(), br.min())
            cross = abs((a.mean(axis=0) - b.mean(axis=0)) @ v)
            if 0.08 < cross <= gap and overlap >= 0.65 * min(np.ptp(ar), np.ptp(br)):
                parent[root(i)] = root(j)
    groups = {}
    for i, record in enumerate(records):
        groups.setdefault(root(i), []).append(record)
    flights = []
    for items in groups.values():
        if not any(code == "66626" for _, _, _, code in items):
            continue
        ids = [item[0] for item in items]
        if len(items) < 2:
            rejected.append(
                {"object_id": ids[0], "reason": "isolated stair line; no measurable flight run"}
            )
            continue
        u = items[0][2]
        v = np.array([-u[1], u[0]])
        all_points = np.concatenate([item[1] for item in items])
        across = all_points @ v
        # Intersection of widths avoids swallowing a neighbouring wall/flight.
        left = max((p @ u).min() for _, p, _, _ in items)
        right = min((p @ u).max() for _, p, _, _ in items)
        if right - left <= 0.3 or np.ptp(across) < 0.1:
            # Name a 66626 nosing: only those can be rebuilt as surveyed single steps.
            nosing = next(item[0] for item in items if item[3] == "66626")
            rejected.append({"object_id": nosing, "reason": "inconsistent flight widths"})
            continue
        centre = (left + right) / 2
        key = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]
        flights.append(
            Flight(
                "stairs:" + key,
                u * centre + v * across.min(),
                u * centre + v * across.max(),
                float(right - left),
                sorted(ids),
            )
        )
    return flights, rejected


class Lidar:
    def __init__(self, positions, classes):
        self.positions = np.asarray(positions, dtype=np.float64)
        self.classes = np.asarray(classes)
        if (
            self.positions.ndim != 2
            or self.positions.shape[1] != 3
            or len(self.positions) != len(self.classes)
        ):
            raise ValueError("lidar requires xyz and one class per point")
        if not np.isfinite(self.positions).all():
            raise ValueError("non-finite lidar")
        self.tree = cKDTree(self.positions[:, [0, 2]])

    def nearby(self, xz, radius, classes=None):
        ids = np.asarray(sorted(self.tree.query_ball_point(xz, radius)), dtype=int)
        if classes is not None:
            ids = ids[np.isin(self.classes[ids], classes)]
        return self.positions[ids]

    def landing(self, xz, *, radius=0.45):
        """Robust plane with alternating fit/holdout points; return independent error.

        Only class 2/12 ground candidates are used. Class 12 is filtered against
        the class-2 plane, preventing roofs and low objects from raising landings.
        """
        p = self.nearby(xz, radius, [2, 12])
        ground = self.nearby(xz, radius, [2])
        if len(ground) < 6 or len(p) < 12:
            raise ValueError(f"insufficient landing lidar at {np.asarray(xz).tolist()}")

        def design(points):
            return np.column_stack((points[:, [0, 2]] - xz, np.ones(len(points))))

        A = design(ground)
        coeff = np.linalg.lstsq(A, ground[:, 1], rcond=None)[0]
        for _ in range(3):
            residual = np.abs(A @ coeff - ground[:, 1])
            good = residual <= max(0.03, 3 * np.median(residual))
            if good.sum() < 6:
                raise ValueError("unstable landing ground fit")
            coeff = np.linalg.lstsq(A[good], ground[good, 1], rcond=None)[0]
        p = p[np.abs(design(p) @ coeff - p[:, 1]) <= 0.15]
        fit, held = p[::2], p[1::2]
        if len(held) < 6:
            raise ValueError("insufficient independent landing samples")
        coeff = np.linalg.lstsq(design(fit), fit[:, 1], rcond=None)[0]
        error = float(np.quantile(np.abs(design(held) @ coeff - held[:, 1]), 0.9))
        if error > 0.05:
            raise ValueError(f"landing held-out p90 residual {error:.3f} m exceeds 0.05 m")
        return float(coeff[2]), error

    def fence_height(self, xz, base, default=1.2):
        p = self.nearby(xz, 0.2, [1])
        heights = p[:, 1] - base
        heights = heights[(heights >= 0.35) & (heights <= 3)]
        if len(heights) < 3:
            return default, "default_missing_class1"
        return float(np.quantile(heights, 0.95)), "class1_p95"

    def wall_top(self, xz):
        p = self.nearby(xz, 0.2, [1, 2, 12])
        if len(p) < 6:
            raise ValueError("insufficient lidar at wall top")
        fit, held = p[::2, 1], p[1::2, 1]
        height = float(np.quantile(fit, 0.9))
        error = abs(height - float(np.quantile(held, 0.9)))
        if error > 0.1:
            raise ValueError(f"wall top held-out error {error:.3f} m exceeds 0.1 m")
        return height, error


def sample_line(line: LineString, spacing=1.0):
    """Densify without replacing original bend vertices."""
    out = []
    points = np.asarray(line.coords)[:, :2]
    for a, b in zip(points[:-1], points[1:], strict=True):
        length = np.linalg.norm(b - a)
        if length < 1e-6:
            continue
        count = max(1, int(np.ceil(length / spacing)))
        out.extend(a + t * (b - a) for t in np.arange(count) / count)
    out.append(points[-1])
    return np.asarray(out)
