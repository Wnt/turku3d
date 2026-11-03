"""Roof and eave returns in the ground key points next to building footprints.

The national ground model can classify a flat roof that continues a higher street
(a roof terrace) and its eave line as ground. Triangulated against the lower open
ground in front of the facade, those returns raise metre-high triangular spikes
along the wall wherever the terrain grid is cut at the footprint edge. Ground under
a footprint is hidden by the building, so its key points only matter where they
shape the visible terrain at the wall.

A key point on or just outside a footprint that stands well above both the open
terrain beyond the eave band and the wall bottom, at the height of the surveyed
LoD2 roof beside it, is lowered to the higher of the two. The wall then still meets
the ground. Planar slopes, uphill walls, terraces and passages below the roof, and
steps beside mapped retaining walls keep their measured points.
"""

from __future__ import annotations

import numpy as np
import shapely
import shapely.ops
from scipy.spatial import cKDTree

BAND = 1.0  # eave overhang and footprint registration error outside a footprint
REACH = 5.0  # outward terrain evidence lies BAND..BAND + REACH from the footprint
RADIUS = 6.0  # horizontal reach of that evidence around each key point
STEP = 1.0  # rise above both the outward terrain and the wall bottom that is not ground
GUARD = 1.5  # a mapped retaining wall this close supports a measured step
ROOF_MATCH = 0.6  # a return this close to the adjacent LoD2 roof plane is that roof
WALLS = {"retaining_wall", "wall", "city_wall"}


def outward_level(xz, refs_xz, refs_y):
    """The open terrain level at xz: the higher of the median and a planar trend."""
    if len(refs_y) < 4:
        return None
    level = float(np.median(refs_y))
    basis = np.c_[np.ones(len(refs_y)), refs_xz - xz]
    if len(refs_y) >= 6 and np.linalg.matrix_rank(basis) == 3:
        fit = np.linalg.lstsq(basis, refs_y, rcond=None)[0]
        level = max(level, float(fit[0]))
    return level


def roof_level(building, x, z):
    """The surveyed roof height nearest to (x, z), or None without a roof within the band."""
    from .roofs import plane_height

    point = shapely.Point(x, z)
    best = None
    for polygon, plane in building.roof_parts or ():
        distance = polygon.distance(point)
        if distance <= BAND + 0.5 and (best is None or distance < best[0]):
            nearest = shapely.ops.nearest_points(polygon, point)[0] if distance else point
            best = distance, float(plane_height(plane, [nearest.x, nearest.y]))
    return None if best is None else best[1]


def guards(barriers=(), retaining=()):
    """Mapped wall lines that make a step beside a footprint real ground."""
    lines = [g for g, tags, *_ in barriers if (tags or {}).get("barrier") in WALLS]
    lines += [g for g, *_ in retaining]
    return [g for g in lines if g is not None and not g.is_empty]


def lower(points, buildings, protected=()):
    """Return (points, changed): roof and eave returns lowered to the visible ground level."""
    out = np.array(points, dtype=float, copy=True)
    changed = np.zeros(len(out), bool)
    if not len(out) or not buildings:
        return out, changed
    xz = out[:, [0, 2]]
    tree = cKDTree(xz)
    guard = shapely.union_all([g.buffer(GUARD) for g in protected]) if protected else None
    if guard is not None:
        shapely.prepare(guard)
    reach = BAND + REACH
    for b in sorted(buildings, key=lambda b: b.key):
        footprint = b.footprint
        if footprint is None or footprint.is_empty:
            continue
        x0, z0, x1, z1 = footprint.bounds
        centre = ((x0 + x1) / 2, (z0 + z1) / 2)
        near = np.asarray(tree.query_ball_point(centre, np.hypot(x1 - x0, z1 - z0) / 2 + reach), int)
        if not len(near):
            continue
        high = near[out[near, 1] > b.base + STEP]
        if not len(high):
            continue
        distance = shapely.distance(footprint, shapely.points(xz[near]))
        candidates = high[shapely.distance(footprint, shapely.points(xz[high])) <= BAND]
        refs = near[(distance > BAND) & (distance <= reach)]
        if not len(candidates) or len(refs) < 4:
            continue
        ref_tree = cKDTree(xz[refs])
        for i in candidates:
            if guard is not None and shapely.contains_xy(guard, *xz[i]):
                continue
            local = refs[ref_tree.query_ball_point(xz[i], RADIUS)]
            level = outward_level(xz[i], xz[local], points[local, 1])
            if level is None:
                continue
            target = max(level, b.base)
            if out[i, 1] <= target + STEP:
                continue
            roof = roof_level(b, *xz[i])
            if roof is not None and abs(out[i, 1] - roof) <= ROOF_MATCH:
                out[i, 1] = target
                changed[i] = True
    return out, changed


def roof_decks(points, buildings):
    """Return (points, changed): interior returns on a building's own roof follow the terrain.

    A drivable roof deck (a garage built into a slope, a roof terrace) classified as
    ground lies on the LoD2 roof plane inside the footprint. Triangulated against the
    street outside it drapes a mound over the walls. Such points take the open terrain
    level interpolated from the ring around the footprint, so neither a mound beside
    the low wall nor a dip beside the uphill wall appears. Positions are unchanged.
    Interior ground at street level (passages beneath the roof) keeps its height.
    """
    from .roofs import plane_height

    out = np.array(points, dtype=float, copy=True)
    changed = np.zeros(len(out), bool)
    if not len(out) or not buildings:
        return out, changed
    xz = out[:, [0, 2]]
    for b in sorted(buildings, key=lambda b: b.key):
        inset = b.footprint.buffer(-0.5) if b.footprint is not None else None
        if inset is None or inset.is_empty:
            continue
        inside = np.flatnonzero(shapely.contains_xy(inset, xz[:, 0], xz[:, 1]))
        inside = inside[out[inside, 1] > b.base + STEP]
        deck = []
        for i in inside:
            point = shapely.Point(*xz[i])
            roof = next(
                (float(plane_height(plane, xz[i])) for polygon, plane in b.roof_parts or ()
                 if polygon.contains(point)),
                None if b.roof_parts else float(b.top),
            )
            if roof is not None and abs(out[i, 1] - roof) <= ROOF_MATCH:
                deck.append(i)
        if not deck:
            continue
        ring = shapely.contains_xy(
            b.footprint.buffer(BAND + REACH).difference(b.footprint.buffer(BAND)), xz[:, 0], xz[:, 1]
        )
        refs = np.flatnonzero(ring & ~changed)
        if len(refs) < 4:
            continue
        tree = cKDTree(xz[refs])
        distance, nearest = tree.query(xz[deck], k=min(8, len(refs)))
        weights = 1 / np.maximum(distance, 0.5) ** 2
        out[deck, 1] = (weights * out[refs][nearest, 1]).sum(axis=1) / weights.sum(axis=1)
        changed[deck] = True
    return out, changed


def terrain_heights(tile, points, heights, buildings, protected=()):
    """The terrain grid without roof decks, roof and eave returns; else the input grid."""
    decks, flat = roof_decks(points, buildings)
    cleaned, changed = lower(decks, buildings, protected)
    changed |= flat
    if not changed.any():
        return heights, 0
    from .inputs import grid_axes, grid_heights

    return grid_heights(cleaned, *grid_axes(tile)), int(changed.sum())
