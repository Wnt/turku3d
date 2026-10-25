"""Phase 0 stub ground: a regular heightfield from the city's ground key points (maanpinta LAZ, class 8).

This is NOT the Phase 1 ground (no curbs, no CDT, no breaklines). It exists so `make tile` can run the
whole chain on real-but-coarse data: the grid is aligned to the origin O with a step that divides the
62.5 m collision tile (default 1.25 m), so neighbouring tiles share bitwise-identical seam vertices.

Interpolation: linear TIN (scipy) + nearest fill outside the hull; without scipy a binned mean +
diffusion fill (numpy only, used by the Mac tests).
"""

from __future__ import annotations

import numpy as np

from turku3d import config as C

STEP = 1.25  # m; 62.5 / 1.25 = 50 cells per collision tile


def heightfield(
    points_xzh: np.ndarray,
    x0: float,
    z0: float,
    nx: int,
    nz: int,
    step: float = STEP,
    *,
    method: str = "auto",
) -> np.ndarray:
    """Heights (nz+1, nx+1) at grid nodes x0 + i*step, z0 + j*step (engine x/z, float64).

    points_xzh: (N,3) engine x, z and height H (N2000).
    """
    pts = np.asarray(points_xzh, np.float64)
    if len(pts) < 3:
        raise ValueError("need at least 3 ground points")
    gx = x0 + np.arange(nx + 1) * step
    gz = z0 + np.arange(nz + 1) * step
    X, Z = np.meshgrid(gx, gz)
    if method in ("auto", "scipy"):
        try:
            from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
        except ImportError:
            if method == "scipy":
                raise
        else:
            lin = LinearNDInterpolator(pts[:, :2], pts[:, 2])
            H = lin(X, Z)
            miss = ~np.isfinite(H)
            if miss.any():
                H[miss] = NearestNDInterpolator(pts[:, :2], pts[:, 2])(X[miss], Z[miss])
            return H
    return _binned(pts, x0, z0, nx, nz, step)


def _binned(pts: np.ndarray, x0: float, z0: float, nx: int, nz: int, step: float) -> np.ndarray:
    ii = np.rint((pts[:, 0] - x0) / step).astype(np.int64)
    jj = np.rint((pts[:, 1] - z0) / step).astype(np.int64)
    ok = (ii >= 0) & (ii <= nx) & (jj >= 0) & (jj <= nz)
    s = np.zeros((nz + 1, nx + 1))
    c = np.zeros_like(s)
    np.add.at(s, (jj[ok], ii[ok]), pts[ok, 2])
    np.add.at(c, (jj[ok], ii[ok]), 1)
    if not c.any():  # all points outside: flat at their median
        return np.full_like(s, float(np.median(pts[:, 2])))
    H = np.where(c > 0, s / np.maximum(c, 1), np.nan)
    known = c > 0
    # diffusion fill: repeatedly average known 4-neighbours into unknown cells
    for _ in range(4 * max(nx, nz)):
        if known.all():
            break
        P = np.pad(np.where(known, H, 0.0), 1)
        K = np.pad(known.astype(float), 1)
        ssum = P[:-2, 1:-1] + P[2:, 1:-1] + P[1:-1, :-2] + P[1:-1, 2:]
        ksum = K[:-2, 1:-1] + K[2:, 1:-1] + K[1:-1, :-2] + K[1:-1, 2:]
        new = (~known) & (ksum > 0)
        H[new] = ssum[new] / ksum[new]
        known = known | new
    return H


def grid_mesh(H: np.ndarray, x0: float, z0: float, step: float = STEP):
    """Triangulate a heightfield: returns (world-local positions (N,3) float64, triangles (M,3) uint32).

    Triangles are counter-clockwise seen from above (+y normal) in the engine frame (x east, z south)."""
    nz1, nx1 = H.shape
    gx = x0 + np.arange(nx1) * step
    gz = z0 + np.arange(nz1) * step
    X, Z = np.meshgrid(gx, gz)
    pos = np.stack([X.ravel(), H.ravel(), Z.ravel()], axis=1)
    i = np.arange(nx1 - 1)
    j = np.arange(nz1 - 1)
    cols, rows = np.meshgrid(i, j)
    a = (rows * nx1 + cols).ravel()
    b = a + 1
    c = a + nx1
    d = c + 1
    # (x,z) a=(0,0) b=(1,0) c=(0,1) d=(1,1); up = +y needs (c - a) x (b - a) ordering: a, c, b
    tris = np.concatenate([np.stack([a, c, b], 1), np.stack([b, c, d], 1)]).astype(np.uint32)
    return pos, tris


def load_ground_points(bbox: C.BBoxLocal, margin: float = 60.0) -> np.ndarray:
    """Engine (x, z, H) of maanpinta key points (class 8) of every LAZ tile touching bbox + margin.

    The points are NOT cropped to bbox + margin: whole LAZ tiles are returned, so neighbouring L0 tiles
    built from the same LAZ tiles interpolate from the identical point set (same TIN, same hull, same
    nearest fill) and their shared seam rows come out bitwise identical. Cropping per tile made the hull
    and the nearest fill differ near the data edge (seen: up to 2.76 m steps across L0 seams)."""
    import sys

    import laspy

    bb = bbox.buffered(margin)
    out, missing = [], []
    for lt in C.laz_tiles_for_bbox(bb):
        f = C.raw_dir("laz") / "maanpinta" / f"{lt.name}.laz"
        if not f.exists():
            missing.append(f)
            continue
        las = laspy.read(f)
        e = np.asarray(las.x, np.float64)
        n = np.asarray(las.y, np.float64)
        h = np.asarray(las.z, np.float64)
        x = e - C.ORIGIN_E
        z = -(n - C.ORIGIN_N)
        out.append(np.stack([x, z, h], 1))
    core = {lt.name for lt in C.laz_tiles_for_bbox(bbox)}
    if any(f.stem in core for f in missing):
        raise FileNotFoundError(f"{[str(f) for f in missing]} missing: run `make fetch-laz` first")
    if missing:
        print(
            f"stub ground: margin LAZ not fetched (ok, nearest fill): {[f.name for f in missing]}",
            file=sys.stderr,
        )
    pts = np.concatenate(out) if out else np.zeros((0, 3))
    if len(pts) < 3:
        raise ValueError(f"no ground key points around {bbox}")
    return pts


def area_heightfield(area: C.Area) -> tuple[np.ndarray, C.BBoxLocal, int]:
    """One canonical grid for an area's visual tiles, cached by source identities and code.

    Every L0 build slices this same grid, even when built separately. The cache avoids repeatedly
    decompressing the LAZ tiles and rebuilding the same Delaunay triangulation.
    """
    import hashlib
    import io
    import json
    from pathlib import Path

    from turku3d.fetch.common import atomic_write_bytes

    tiles = area.visual_tiles
    bb = C.BBoxLocal(
        min(t.bbox.xmin for t in tiles),
        min(t.bbox.zmin for t in tiles),
        max(t.bbox.xmax for t in tiles),
        max(t.bbox.zmax for t in tiles),
    )
    sources = [
        C.raw_dir("laz") / "maanpinta" / f"{t.name}.laz"
        for t in C.laz_tiles_for_bbox(bb.buffered(C.MESH_MARGIN))
    ]
    identities = [
        (str(p), p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else (str(p), None)
        for p in sources
    ]
    key = hashlib.sha256(
        Path(__file__).read_bytes()
        + json.dumps([identities, bb.as_tuple(), STEP, C.MESH_MARGIN]).encode()
    ).hexdigest()
    path = C.cache_dir("stub-ground") / f"{key}.npz"
    if path.exists():
        with np.load(path, allow_pickle=False) as data:
            return data["height"], bb, int(data["point_count"])
    pts = load_ground_points(bb, margin=C.MESH_MARGIN)
    H = heightfield(
        pts,
        bb.xmin,
        bb.zmin,
        round((bb.xmax - bb.xmin) / STEP),
        round((bb.zmax - bb.zmin) / STEP),
        method="scipy",
    )
    buffer = io.BytesIO()
    np.savez(buffer, height=H, point_count=len(pts))
    atomic_write_bytes(path, buffer.getvalue())
    return H, bb, len(pts)
