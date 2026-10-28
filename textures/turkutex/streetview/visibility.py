"""LOD2 ray visibility of a wall's full extent, including self-occluding wings."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from threading import Lock

import numpy as np
from turku3d import config

_LOAD_LOCK = Lock()


def blocked_rays(
    camera: np.ndarray, targets: np.ndarray, triangles: np.ndarray, *, target_band: bool = False
) -> np.ndarray:
    """Segment/triangle intersections; ignore the last 0.4 m of each target ray.

    The tolerance covers wall quantisation and footprint chords, not neighbouring
    building IDs: another wing of the same building can hide the target wall.
    """
    targets = np.asarray(targets, dtype=float).reshape(-1, 3)
    result = np.zeros(len(targets), dtype=bool)
    rays = targets - camera
    lengths = np.linalg.norm(rays, axis=1)
    for start in range(0, len(triangles), 128):
        tri = triangles[start : start + 128]
        edge1, edge2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        cross = np.cross(rays[:, None], edge2)
        det = np.einsum("rti,ti->rt", cross, edge1)
        inverse = np.divide(1, det, out=np.zeros_like(det), where=np.abs(det) > 1e-9)
        offset = camera - tri[:, 0]
        u = np.einsum("ti,rti->rt", offset, cross) * inverse
        q = np.cross(offset, edge1)
        v = np.einsum("ri,ti->rt", rays, q) * inverse
        t = np.einsum("ti,ti->t", edge2, q)[None] * inverse
        near = (lengths - 0.5) if target_band else np.full(len(lengths), 0.05)
        far = (lengths + 0.5) if target_band else lengths - 0.4
        hit = (
            (np.abs(det) > 1e-9)
            & (u >= -1e-7)
            & (v >= -1e-7)
            & (u + v <= 1 + 1e-7)
            & (t > near[:, None] / np.maximum(lengths[:, None], 0.05))
            & (t < far[:, None] / np.maximum(lengths[:, None], 0.05))
        )
        result |= hit.any(axis=1)
    return result


def wall_samples(corners: np.ndarray) -> np.ndarray:
    """Include edge strips as well as the centre, so a clear centre is insufficient."""
    u, v = np.meshgrid(np.linspace(0.01, 0.99, 13), np.linspace(0.01, 0.99, 9))
    return (
        corners[0]
        + u[..., None] * (corners[1] - corners[0])
        + v[..., None] * (corners[3] - corners[0])
    )


class Scene:
    def __init__(self, triangles: np.ndarray, fingerprint: str):
        import shapely

        self.triangles = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
        self.fingerprint = fingerprint
        lo, hi = self.triangles[:, :, :2].min(1), self.triangles[:, :, :2].max(1)
        self.tree = shapely.STRtree(shapely.box(lo[:, 0], lo[:, 1], hi[:, 0], hi[:, 1]))

    def measure(self, camera: np.ndarray, corners: np.ndarray) -> dict:
        import shapely

        points = np.vstack([camera, corners])
        lo, hi = points[:, :2].min(0), points[:, :2].max(0)
        indices = self.tree.query(shapely.box(*lo, *hi))
        grid = wall_samples(corners)
        blocked = blocked_rays(camera, grid, self.triangles[indices]).reshape(grid.shape[:2])
        edge = np.concatenate([blocked[0], blocked[-1], blocked[:, 0], blocked[:, -1]])
        supported = blocked_rays(camera, grid, self.triangles[indices], target_band=True)
        return dict(
            target_support=round(float(supported.mean()), 4),
            visible_fraction=round(float(1 - blocked.mean()), 4),
            edge_visible_fraction=round(float(1 - edge.mean()), 4),
            triangles=len(indices),
        )


def source_files() -> tuple:
    paths = sorted(config.raw_dir("wfs").glob("*/bldg_Building_LOD2.parquet"))
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def fingerprint() -> str:
    return hashlib.sha256(repr(source_files()).encode()).hexdigest()


def triangulate_rings(rings):
    """Planar source polygons for visibility, preserving concavities and holes.

    These triangles are only occlusion proxies, never a reconstructed shell or
    shipped geometry. Coordinates are translated before computing the normal.
    """
    import mapbox_earcut

    rings = [r[:-1] if np.array_equal(r[0], r[-1]) else r for r in rings]
    if any(len(r) < 3 for r in rings):
        raise ValueError("degenerate surface")
    points = np.concatenate(rings)
    local = rings[0] - rings[0][0]
    normal = np.cross(local, np.roll(local, -1, axis=0)).sum(0)
    norm = np.linalg.norm(normal)
    if norm < 1e-8 or not np.isfinite(points).all():
        raise ValueError("invalid surface")
    xy = np.ascontiguousarray(np.delete(points - points[0], np.argmax(np.abs(normal)), axis=1))
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    return points, mapbox_earcut.triangulate_float64(xy, ends).reshape(-1, 3)


@lru_cache(maxsize=16)
def load_scene(files: tuple, bounds: tuple) -> Scene:
    import geopandas as gpd
    import pyarrow.parquet as pq

    cache_key = hashlib.sha256(repr((files, bounds, "triangles-v1")).encode()).hexdigest()
    cached = config.work_root() / "cache/reference-visibility" / (cache_key + ".npy")
    if cached.exists():
        return Scene(np.load(cached, allow_pickle=False), cache_key)
    triangles, seen = [], set()
    origin = np.array([config.ORIGIN_E, config.ORIGIN_N, 0.0])
    for name, _, _ in files:
        if "surface_type" not in pq.read_schema(name).names:
            continue
        frame = gpd.read_parquet(Path(name), columns=["geometry", "surface_type"])
        box = frame.total_bounds
        if not overlaps(box, bounds):
            continue
        frame = frame.cx[bounds[0] : bounds[2], bounds[1] : bounds[3]]
        for geometry, kind in zip(frame.geometry, frame.surface_type, strict=True):
            if kind not in ("WallSurface", "RoofSurface") or geometry.geom_type != "Polygon":
                continue
            key = geometry.wkb
            if key in seen:
                continue
            seen.add(key)
            rings = [
                np.asarray(r.coords) - origin for r in [geometry.exterior, *geometry.interiors]
            ]
            if any(r.shape[1] != 3 for r in rings):
                continue
            try:
                positions, indices = triangulate_rings(rings)
            except ValueError:
                continue
            triangles.extend(positions[indices] + origin)
    from turku3d.region.cache import atomic_target

    triangles = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    with atomic_target(cached) as temporary:
        with temporary.open("wb") as stream:
            np.save(stream, triangles, allow_pickle=False)
    return Scene(triangles, cache_key)


def overlaps(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


@lru_cache(maxsize=2048)
def file_bounds(record):
    import pyarrow.parquet as pq

    schema = pq.read_schema(record[0])
    if "surface_type" not in schema.names:
        return None
    return json.loads(schema.metadata[b"geo"])["columns"]["geometry"].get("bbox")


def wall_visibility(camera: np.ndarray, corners: np.ndarray) -> dict:
    centre = corners.mean(0)
    west, south = np.floor(centre[:2] / 500) * 500
    reach = max(250, np.ceil(np.linalg.norm(camera[:2] - centre[:2]) / 250) * 250)
    bounds = (west - reach, south - reach, west + 500 + reach, south + 500 + reach)
    files = tuple(r for r in source_files() if (b := file_bounds(r)) and overlaps(b, bounds))
    if not files:
        return dict(state="unavailable", reason="no local LOD2 surfaces")
    with _LOAD_LOCK:
        scene = load_scene(files, bounds)
    return dict(state="measured", **scene.measure(camera, corners))
