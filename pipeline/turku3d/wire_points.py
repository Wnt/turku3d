"""Overhead-wire returns in a survey point cloud (the survey has no wire class: codes 1-9 and 12 only).

Shared by the point-cloud product's wire layer (``tools/pointcloud/build_wires.py``) and the generator's
overhead wires (``turku3d.coarse.wires``), so both see the same wires:
  1. candidates: unclassified (1) and overlap (12) returns more than 3 m above a 2 m ground grid (class 2 minima);
  2. free air: at most one classified return (anything but 1, 7, 12) in the 1.5 m cube around (0.5 m voxels),
     and with ``self_crowd`` at most that many of the 27 voxels occupied by candidates;
  3. line-like: >= 4 candidates within 1.2 m whose covariance has linearity (l1 - l2) / l1 > 0.85 and a
     near-horizontal principal axis (|z| < 0.5);
  4. long: connected (1.5 m) stretches at least 8 m across with at least 10 returns (drops branch-like specks).
Coordinates: ``x``, ``y`` in plan and ``z`` up. ``origin`` anchors the ground and voxel grids; None anchors them at
the input's own minimum (the product's per-LAZ-tile layer), a fixed origin makes crops of one area agree.
Only numpy and scipy: the point-cloud tools import this without the pipeline's other dependencies.
"""
import hashlib
import json

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

RECIPE = {'version': 'wires@1', 'min_hag': 3.0, 'voxel': 0.5, 'crowd': 1, 'radius': 1.2, 'min_neighbours': 4,
          'linearity': 0.85, 'max_dz': 0.5, 'link': 1.5, 'min_extent': 8.0, 'min_points': 10, 'class': 14}
RECIPE_KEY = hashlib.sha256(json.dumps(RECIPE, sort_keys=True).encode()).hexdigest()[:16]
WIRE_RGB = (40 << 8, 40 << 8, 46 << 8)


def height_above_ground(x, y, z, cls, origin=None):
    g = cls == 2
    x0, y0 = (np.floor(x.min()), np.floor(y.min())) if origin is None else origin
    gx, gy = ((x - x0) // 2).astype(np.int64), ((y - y0) // 2).astype(np.int64)
    ground = np.full((gx.max() + 1, gy.max() + 1), np.inf)
    if g.any():
        np.minimum.at(ground, (gx[g], gy[g]), z[g])
    holes = ~np.isfinite(ground)
    if holes.all():
        return z - z.min()
    if holes.any():
        ground = ground[tuple(ndimage.distance_transform_edt(holes, return_distances=False, return_indices=True))]
    return z - ground[gx, gy]


def voxel_keys(ix, iy, iz):
    return (ix << 42) | ((iy & 0x1fffff) << 21) | (iz & 0x1fffff)


def uncrowded(ix, iy, iz, occupied, counts, limit):
    """The original 27-voxel count test, once per candidate cell with early rejection.

    Many returns occupy the same voxel. Once a cell exceeds the threshold,
    further nonnegative counts cannot make it eligible again. Keep source
    return order through the inverse map; only the amount of searching changes.
    """
    _, first, inverse = np.unique(voxel_keys(ix, iy, iz), return_index=True, return_inverse=True)
    x, y, z = ix[first], iy[first], iz[first]
    total = np.zeros(len(first), np.int64)
    keep = np.ones(len(first), bool)
    if not len(occupied):
        return keep[inverse]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                rows = np.flatnonzero(keep)
                if not len(rows):
                    return keep[inverse]
                keys = voxel_keys(x[rows] + dx, y[rows] + dy, z[rows] + dz)
                index = np.minimum(np.searchsorted(occupied, keys), len(occupied) - 1)
                hit = occupied[index] == keys
                total[rows] += hit if counts is None else np.where(hit, counts[index], 0)
                keep[rows] = total[rows] <= limit
    return keep[inverse]


def wire_points(x, y, z, cls, hag, r=RECIPE, origin=None, workers=2):
    """Indices of wire returns and their stretch (segment) ids, dense from 0."""
    cand = np.flatnonzero(((cls == 1) | (cls == 12)) & (hag > r['min_hag']))
    others = np.flatnonzero((hag > 1.0) & (cls != 1) & (cls != 7) & (cls != 12))
    if not len(cand):
        return cand, cand
    v = r['voxel']
    x0, y0 = (np.floor(x.min()), np.floor(y.min())) if origin is None else origin
    def vox(idx):
        return (np.floor((x[idx] - x0) / v).astype(np.int64), np.floor((y[idx] - y0) / v).astype(np.int64),
                np.floor((z[idx] + 100) / v).astype(np.int64))
    uk, uc = np.unique(voxel_keys(*vox(others)), return_counts=True) if len(others) else (np.zeros(1, np.int64) - 1, np.zeros(1, np.int64))
    cx, cy, cz = vox(cand)
    iso = cand[uncrowded(cx, cy, cz, uk, uc, r['crowd'])]
    if 'self_crowd' in r and len(iso):
        # Optional: candidates crowd themselves too. Strip-overlap returns (12) fill whole roofs and crowns where
        # no classified return is left to crowd them; a wire occupies a thin line of voxels.
        ix, iy, iz = vox(iso)
        own = voxel_keys(ix, iy, iz)
        uo = np.unique(own)
        iso = iso[uncrowded(ix, iy, iz, uo, None, r['self_crowd'])]
    if len(iso) < r['min_points']:
        return iso[:0], iso[:0]
    P = np.c_[x[iso], y[iso], z[iso]]
    nbrs = cKDTree(P).query_ball_point(P, r['radius'], workers=workers)
    keep = np.zeros(len(iso), bool)
    for i, n in enumerate(nbrs):
        if len(n) < r['min_neighbours']:
            continue
        Q = P[n] - P[n].mean(0)
        w, vec = np.linalg.eigh(Q.T @ Q)
        keep[i] = (w[2] - w[1]) / max(w[2], 1e-9) > r['linearity'] and abs(vec[2, 2]) < r['max_dz']
    W, idx = P[keep], iso[keep]
    if len(W) < r['min_points']:
        return idx[:0], idx[:0]
    pairs = cKDTree(W).query_pairs(r['link'], output_type='ndarray')
    n = len(W)
    comp = connected_components(coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)), directed=False)[1]
    order = np.argsort(comp, kind='stable')
    starts = np.flatnonzero(np.r_[True, np.diff(comp[order]) != 0])
    good = np.zeros(comp.max() + 1, bool)
    for s, e in zip(starts, np.r_[starts[1:], n], strict=True):
        q = W[order[s:e], :2]
        good[comp[order[s]]] = e - s >= r['min_points'] and np.hypot(*(q.max(0) - q.min(0))) >= r['min_extent']
    mask = good[comp]
    # Dense segment ids in this tile.
    _, segment = np.unique(comp[mask], return_inverse=True)
    return idx[mask], segment.astype(np.uint32)
