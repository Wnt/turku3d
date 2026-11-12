"""Bounded raw-ground evidence for semantic supports; no city-wide raster build.

Samples retain actual surveyed XYZ. A fixed metre grid reduces dense returns,
preserving two supported height populations when a cell straddles a step. The
existing vector breaklines and retaining survey still own discontinuities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

VERSION = 'turku-ground-survey/1'


def ground_returns(points, classes):
    """Keep class 2 and only overlapping class 12 backed by nearby ground."""
    ground = points[classes == 2]
    overlap = points[classes == 12]
    if not len(ground) or not len(overlap):
        return ground
    # Overlap is a flight-strip label, not a ground classification. A generous
    # distance-scaled height gate admits parked-car skirts on sparse roads.
    # Accept only agreement with a locally planar class-2 neighbourhood; near
    # discontinuities retain the conservative nearest-return alternative.
    tree = cKDTree(ground[:, [0, 2]])
    keep = np.zeros(len(overlap), dtype=bool)
    k = min(12, len(ground))
    for start in range(0, len(overlap), 32768):
        part = overlap[start:start + 32768]
        distance, owner = tree.query(part[:, [0, 2]], k=k)
        distance, owner = distance.reshape(len(part), k), owner.reshape(len(part), k)
        neighbours = ground[owner]
        nearest_delta = abs(part[:, 1] - neighbours[:, 0, 1])
        accepted = (distance[:, 0] < .3) & (nearest_delta < .08)
        if k >= 6:
            local = neighbours[:, :, [0, 2]] - part[:, None, [0, 2]]
            design = np.concatenate([local, np.ones((*local.shape[:2], 1))], axis=2)
            weights = (distance < 1.5).astype(float)
            lhs = np.einsum('nki,nk,nkj->nij', design, weights, design)
            rhs = np.einsum('nki,nk,nk->ni', design, weights, neighbours[:, :, 1])
            # Tiny diagonal regularisation handles collinear/sparse candidates;
            # geometric rank below still rejects those fits explicitly.
            coefficients = np.linalg.solve(lhs + np.eye(3) * 1e-10, rhs[..., None])[..., 0]
            residual = np.einsum('nki,ni->nk', design, coefficients) - neighbours[:, :, 1]
            count = weights.sum(axis=1)
            rms = np.sqrt((weights * residual**2).sum(axis=1) / np.maximum(count, 1))
            rank = np.linalg.eigvalsh(lhs)[:, 0]
            planar = ((count >= 6) & (rank > .01) & (rms < .04)
                      & (np.linalg.norm(coefficients[:, :2], axis=1) < 1.)
                      & (distance[:, 0] < 1.))
            accepted |= planar & (abs(part[:, 1] - coefficients[:, 2]) < .08)
        keep[start:start + len(part)] = accepted
    return np.concatenate([ground, overlap[keep]])


def representative_returns(points, spacing=1.):
    """Deterministic source returns near cell centres and the dominant height.

The grid is anchored to the project origin, so adjacent source windows agree.
One well-supported vertical gap permits two samples rather than averaging a
lower quay and an upper pavement into an invented intermediate elevation.
"""
    points = np.unique(np.asarray(points, dtype=np.float64), axis=0)
    if not len(points):
        return points.reshape(-1, 3)
    cells = np.floor(points[:, [0, 2]] / spacing).astype(np.int64)
    order = np.lexsort((points[:, 2], points[:, 1], cells[:, 1], cells[:, 0]))
    points, cells = points[order], cells[order]
    cuts = np.r_[0, np.flatnonzero(np.any(cells[1:] != cells[:-1], axis=1)) + 1, len(points)]
    selected = []
    for start, end in zip(cuts[:-1], cuts[1:], strict=True):
        group = points[start:end]
        by_height = group[np.argsort(group[:, 1], kind='stable')]
        gaps = np.diff(by_height[:, 1])
        split = int(np.argmax(gaps)) + 1 if len(gaps) else 0
        groups = [group]
        if split >= 3 and len(group) - split >= 3 and gaps[split - 1] > .3:
            groups = [by_height[:split], by_height[split:]]
        centre = (cells[start] + .5) * spacing
        for part in groups:
            median = np.median(part[:, 1])
            score = np.sum((part[:, [0, 2]] - centre) ** 2, axis=1)
            score += ((part[:, 1] - median) / .15) ** 2
            selected.append(part[np.argmin(score)])
    return np.asarray(selected)


def freeze(output, bounds, spacing=1., halo=8.):
    from turku3d import config as C
    from turku3d.ground.inputs import laz_paths, read_returns

    bounds = np.asarray(bounds, dtype=float)
    if len(bounds) != 4 or not np.isfinite(bounds).all() or np.any(bounds[2:] <= bounds[:2]):
        raise ValueError('Ground survey needs finite nonempty bounds')
    if spacing <= 0 or spacing > 2 or halo < 2:
        raise ValueError('Ground survey requires spacing in (0,2] and at least 2m halo')
    source_bounds = bounds + [-halo, -halo, halo, halo]
    box = C.BBoxLocal(*source_bounds)
    paths = laz_paths(box)
    if not paths:
        raise ValueError('No local raw lidar covers the requested ground survey')
    points, classes = read_returns(paths, box, [2, 12])
    ground = ground_returns(points, classes)
    samples = representative_returns(ground, spacing)
    if len(samples) < 3:
        raise ValueError('Insufficient raw ground survey returns')
    provenance = dict(version=VERSION, bounds=bounds.tolist(), sourceBounds=source_bounds.tolist(),
                      spacingM=spacing, haloM=halo, heightDatum='N2000', classes=[2, 12],
                      rawReturns=len(points), acceptedReturns=len(ground), samples=len(samples),
                      overlapQualification='class2-local-plane-8cm/1',
                      class2Returns=int(np.count_nonzero(classes == 2)),
                      rejectedOverlapReturns=int(len(points) - len(ground)),
                      sources=[dict(name=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, points=samples, provenance=json.dumps(provenance, sort_keys=True))
    return provenance


def load(path, bounds):
    """Load pinned local survey evidence without hiding an uncovered pilot."""
    with np.load(path, allow_pickle=False) as data:
        points = data['points']
        provenance = json.loads(str(data['provenance']))
    if provenance.get('version') != VERSION:
        raise ValueError('Unsupported ground survey')
    source = provenance['bounds']
    if bounds[0] < source[0] or bounds[1] < source[1] or bounds[2] > source[2] or bounds[3] > source[3]:
        raise ValueError('Ground survey does not cover the complete pilot')
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError('Invalid ground survey positions')
    return points, provenance


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bounds', nargs=4, type=float, required=True)
    parser.add_argument('--spacing', type=float, default=1.)
    args = parser.parse_args()
    print(json.dumps(freeze(args.output, args.bounds, args.spacing), indent=2))
