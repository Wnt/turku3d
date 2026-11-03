"""Static ambient visibility baked into vertex COLOR_1, independent of pigment and sun.

A 2.5D height field (terrain plus every LOD2 roof part within the 100 m building context)
replaces ray casting: each vertex marches a fixed set of azimuths outward and keeps the
highest horizon elevation, near (contact/crease occlusion) and far (street-canyon sky).
Cosine-weighted visibility of the normal's hemisphere above those horizons gives

- R, local ambient visibility: hemisphere occluded within ``LOCAL`` metres, including the
  ground below a wall's base, relative to an unoccluded hemisphere;
- G, sky visibility: sky above the far horizon relative to an open flat field for the
  same normal (1 on an open square or roof, lower at the bottom of a narrow street);
- B, coverage: 1 where this bake applies.

Work is bounded and vectorised in chunks, with no per-triangle traversal and no native
dependency: each distinct start point does ``AZIMUTHS`` × ``STEPS`` height lookups, each
distinct (position, normal) one slope maximum per azimuth, and the fixed elevation table is
integrated in O(1) per azimuth from prefix sums over each distinct normal's bin range. The
building context of ``inputs.wall_context`` reaches 100 m beyond the tile, beyond ``FAR``,
so vertices on both sides of a tile border see the same occluders. Terrain beyond the
tile grid repeats its edge heights; bridges, trees and tunnels are not occluders yet.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import shapely

CELL = 1.0  # height-field resolution, metres
FAR = 40.0  # sky horizon reach, metres (< the 100 m building context)
LOCAL = 3.0  # contact/crease occlusion reach, metres
AZIMUTHS = 16
# Geometric march from just outside the start point to FAR; the first STEPS_LOCAL are within LOCAL.
STEPS = np.array([0.75, 1.0, 1.4, 2.0, 2.9, 4.0, 5.6, 8.0, 11.0, 15.0, 21.0, 29.0, 40.0])
STEPS_LOCAL = int(np.searchsorted(STEPS, LOCAL, side="right"))
ELEVATIONS = 24  # elevation bins over [-90°, 90°] for the cosine-weighted hemisphere integral
WALL_OFFSET = 0.5  # metres a start point steps away from its own wall along the horizontal normal
# Roof parts rasterise inset by half a cell: a footprint-edge vertex (wall base, ground boundary,
# grazing wall direction) must not sample its own building's cell as an occluder at zero range.
INSET = 0.5 * CELL
GROUND_CLEARANCE = 0.25  # metres; start points never lie below the terrain plus this
CHUNK = 4096  # rows per batch: (CHUNK, STEPS * AZIMUTHS) float64 temporaries stay cache-sized
# Layers whose surfaces are not lit by the open sky (tunnel interiors keep their authored light).
SKIP_LAYERS = frozenset({"tunnels"})
SKIP_MATERIALS = frozenset({"tunnel_concrete", "water"})


@dataclass(frozen=True)
class HeightField:
    """Max occluder height on a regular x/z grid in the tile frame (row = z, column = x)."""

    x0: float
    z0: float
    heights: np.ndarray  # (rows, cols) float32
    terrain: np.ndarray  # (rows, cols) float32, ground only

    def sample(self, grid, x, z):
        col = np.clip(((x - self.x0) / CELL).astype(np.int32), 0, grid.shape[1] - 1)
        row = np.clip(((z - self.z0) / CELL).astype(np.int32), 0, grid.shape[0] - 1)
        return grid[row, col]


def height_field(tile, buildings, terrain_heights) -> HeightField:
    """Rasterise terrain and every roof part around ``tile`` (tile-local frame).

    ``buildings`` are inputs.Building objects in engine x/z with ``roof_parts`` of
    (polygon, plane); ``terrain_heights`` is the tile's (CELLS+1)² grid, row = z.
    """
    from .roofs import plane_height

    ox, oz = float(tile.origin[0]), float(tile.origin[2])
    pad = FAR + 2 * CELL
    x0, z0 = -pad, -pad
    count = int(np.ceil((tile.size + 2 * pad) / CELL))
    centres = (np.arange(count) + 0.5) * CELL
    xs, zs = x0 + centres, z0 + centres
    grid = np.asarray(terrain_heights, float)
    step = tile.size / (grid.shape[0] - 1)
    # Bilinear terrain, clamped to the tile grid's edge outside it.
    fx = np.clip(xs / step, 0, grid.shape[1] - 1 - 1e-6)
    fz = np.clip(zs / step, 0, grid.shape[0] - 1 - 1e-6)
    ix, iz = fx.astype(int), fz.astype(int)
    tx, tz = (fx - ix)[None, :], (fz - iz)[:, None]
    a, b = grid[iz][:, ix], grid[iz][:, ix + 1]
    c, d = grid[iz + 1][:, ix], grid[iz + 1][:, ix + 1]
    terrain = ((a * (1 - tx) + b * tx) * (1 - tz) + (c * (1 - tx) + d * tx) * tz).astype(np.float32)
    heights = terrain.copy()
    for building in buildings:
        for polygon, plane in building.roof_parts:
            part = polygon.intersection(building.footprint) if building.footprint is not None else polygon
            part = part.buffer(-INSET)
            if part.is_empty:
                continue
            lx0, lz0, lx1, lz1 = part.bounds
            c0 = max(0, int(np.floor((lx0 - ox - x0) / CELL)))
            c1 = min(count, int(np.ceil((lx1 - ox - x0) / CELL)) + 1)
            r0 = max(0, int(np.floor((lz0 - oz - z0) / CELL)))
            r1 = min(count, int(np.ceil((lz1 - oz - z0) / CELL)) + 1)
            if c0 >= c1 or r0 >= r1:
                continue
            gx, gz = np.meshgrid(xs[c0:c1] + ox, zs[r0:r1] + oz)
            inside = shapely.contains_xy(part, gx, gz)
            if not inside.any():
                continue
            top = plane_height(plane, np.stack([gx[inside], gz[inside]], axis=1))
            top = np.clip(top, building.base, max(building.top, building.base))
            block = heights[r0:r1, c0:c1]
            block[inside] = np.maximum(block[inside], top.astype(np.float32))
    return HeightField(x0, z0, heights, terrain)


_ANGLES = np.linspace(-np.pi / 2, np.pi / 2, ELEVATIONS + 1)
_THETA = (_ANGLES[:-1] + _ANGLES[1:]) / 2
_BIN = np.pi / ELEVATIONS
_PHI = (np.arange(AZIMUTHS) + 0.5) * 2 * np.pi / AZIMUTHS
_DIRECTIONS = np.stack([np.cos(_PHI), np.sin(_PHI)], axis=1)  # (K, 2) x/z
# March offsets of every (step, azimuth) pair, step-major, and the step distance of each.
_DX = (STEPS[:, None] * _DIRECTIONS[None, :, 0]).ravel()
_DZ = (STEPS[:, None] * _DIRECTIONS[None, :, 1]).ravel()
_REACH = np.repeat(STEPS, AZIMUTHS)
# A normal's cosine-weighted bin weight (a along the azimuth, u up) is max(a cos t + u sin t, 0)
# cos t dt; prefix sums of its two terms give the sum over any bin range in O(1).
_COS2 = np.concatenate([[0], np.cumsum(np.cos(_THETA) ** 2 * _BIN)])
_SINCOS = np.concatenate([[0], np.cumsum(np.sin(_THETA) * np.cos(_THETA) * _BIN)])
_HORIZONTAL = ELEVATIONS // 2  # bins below this index lie under the horizontal


def _below(angle):
    """Number of elevation bin centres at or below ``angle`` (any shape)."""
    return np.clip((angle + np.pi / 2) / _BIN + 0.5, 0, ELEVATIONS).astype(np.intp)


def _unique(values):
    """Exact unique rows of a float64 (N, d) array.

    Returns (each group's first row, each row's group, rows ordered by group); groups are numbered
    in that order, so rows of one group are contiguous in it.
    """
    bits = np.ascontiguousarray(values, np.float64).view(np.uint64)
    key = bits[:, 0].copy()
    for column in range(1, bits.shape[1]):
        key ^= key >> np.uint64(29)
        key *= np.uint64(0x9E3779B97F4A7C15)
        key ^= bits[:, column]
    order = np.argsort(key, kind="stable")
    ordered = key[order]
    new = np.ones(len(key), bool)
    np.not_equal(ordered[1:], ordered[:-1], out=new[1:])
    group = np.empty(len(key), np.intp)
    group[order] = np.cumsum(new) - 1
    first = order[new]
    if not np.array_equal(bits[first[group]], bits):  # a 64-bit key collision: exact fallback
        _, first, group = np.unique(bits, axis=0, return_index=True, return_inverse=True)
        group = group.reshape(-1)
        order = np.argsort(group, kind="stable")
    return first, group, order


_MARGIN = 64  # cells of edge replication around the field: a lookup there equals the clamped one


def _padded(grid):
    return np.pad(grid, _MARGIN, mode="edge").ravel()


def _march(field, padded, start):
    """(m, STEPS * AZIMUTHS) float32 occluder heights around (m, 2) start points.

    Same cells as ``HeightField.sample``; ``padded`` (``_padded(field.heights)``) makes its
    clamp a plain lookup, and coordinates beyond the margin are clamped into it first.
    """
    rows, cols = field.heights.shape
    width = cols + 2 * _MARGIN
    col = start[:, 0, None] + _DX
    col -= field.x0
    row = start[:, 1, None] + _DZ
    row -= field.z0
    if CELL != 1.0:
        col /= CELL
        row /= CELL
    reach = STEPS[-1] / CELL + 1
    low, high = start.min(axis=0), start.max(axis=0)
    if (min((low[0] - field.x0) / CELL, (low[1] - field.z0) / CELL) - reach <= -_MARGIN
            or (high[0] - field.x0) / CELL + reach >= cols + _MARGIN - 1
            or (high[1] - field.z0) / CELL + reach >= rows + _MARGIN - 1):
        np.clip(col, -_MARGIN, cols + _MARGIN - 1, out=col)
        np.clip(row, -_MARGIN, rows + _MARGIN - 1, out=row)
    np.trunc(row, out=row)
    row += _MARGIN
    row *= width
    np.trunc(col, out=col)
    row += col
    row += _MARGIN
    return padded.take(row.astype(np.intp))


def _band(hemisphere, first):
    """Cosine-weighted solid angle (n,) of the bins from ``first`` (n, K) up to each azimuth's top."""
    horizontal, up, low, high, top = hemisphere
    begin = np.minimum(np.maximum(low, first), high)
    return (top - (horizontal * _COS2[begin] + up * _SINCOS[begin])).sum(axis=1)


def _hemisphere(normals):
    """Per unit normal: its components along each azimuth and up, and its bin range [low, high).

    The positive part of a cos t + u sin t is one contiguous elevation range per azimuth.
    """
    flat = np.hypot(normals[:, 0], normals[:, 2])
    lateral = np.where(flat[:, None] > 1e-3, normals[:, [0, 2]] / np.maximum(flat, 1e-9)[:, None], 0)
    horizontal = lateral @ _DIRECTIONS.T * flat[:, None]
    up = normals[:, 1, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        edge = _below(np.arctan(-horizontal / up)).astype(np.int8)
    low = np.where(up > 0, edge, np.int8(0))
    high = np.where(up < 0, edge, np.where((up > 0) | (horizontal > 0), np.int8(ELEVATIONS), np.int8(0)))
    top = horizontal * _COS2[high] + up * _SINCOS[high]
    return flat, lateral, (horizontal, up, low, high, top)


def _visibility(height, march, hemisphere, whole, open_sky):
    """(n, 2) R/G from eye heights, the occluder heights of each row's start point and its normal.

    Slopes stand in for elevation angles: the bins only compare against the steepest one, and
    arctan is monotonic.
    """
    slope = march.astype(np.float64)
    slope -= height[:, None]
    slope /= _REACH
    slope = slope.reshape(len(height), len(STEPS), AZIMUTHS)
    near = slope[:, :STEPS_LOCAL].max(axis=1)
    far = np.maximum(near, slope[:, STEPS_LOCAL:].max(axis=1))
    local = _band(hemisphere, _below(np.arctan(near)))
    sky = _band(hemisphere, np.maximum(_below(np.arctan(far)), _HORIZONTAL))
    ambient = np.where(whole > 1e-6, local / np.maximum(whole, 1e-9), 1)
    # A face that sees no open sky (downward) keeps its ambient; R still records its creases.
    return np.stack([ambient, np.where(open_sky > 1e-6, sky / np.maximum(open_sky, 1e-9), 1)], axis=1)


def sample(field: HeightField, positions, normals):
    """(N,3) float32 visibility for tile-frame ``positions`` with unit ``normals``.

    A pure function of each (position, normal) row: repeated rows (split normals, seams shared
    by meshes) are evaluated once, rows with equal start points share one march and rows with
    equal normals one hemisphere.
    """
    positions = np.asarray(positions, np.float64).reshape(-1, 3)
    normals = np.asarray(normals, np.float64).reshape(-1, 3)
    result = np.ones((len(positions), 3), np.float32)
    if not len(positions):
        return result
    first, inverse, _ = _unique(np.concatenate([positions, normals], axis=1))
    p = positions[first]
    kinds, kind, _ = _unique(normals[first])
    n = normals[first[kinds]]
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(length > 1e-6, n / np.maximum(length, 1e-9), [0.0, 1.0, 0.0])
    flat, lateral, hemisphere = _hemisphere(n)
    whole = _band(hemisphere, np.zeros((1, 1), np.int8))
    open_sky = _band(hemisphere, np.full((1, 1), _HORIZONTAL, np.int8))
    start = p[:, [0, 2]] + lateral[kind] * (WALL_OFFSET * flat[kind])[:, None]
    ground = field.sample(field.terrain, start[:, 0], start[:, 1])
    height = np.maximum(p[:, 1] + 0.1 * np.maximum(n[kind, 1], 0), ground + GROUND_CLEARANCE)
    origin, ids, order = _unique(start)  # rows sharing a start point are contiguous in order
    padded = _padded(field.heights)
    values = np.empty((len(first), 2), np.float32)
    for begin in range(0, len(order), CHUNK):
        rows = order[begin:begin + CHUNK]
        chunk = ids[rows]
        march = _march(field, padded, start[origin[chunk[0]:chunk[-1] + 1]])[chunk - chunk[0]]
        k = kind[rows]
        values[rows] = _visibility(height[rows], march, tuple(a[k] for a in hemisphere), whole[k], open_sky[k])
    result[:, :2] = values[inverse]
    np.clip(result, 0, 1, out=result)
    return result


def eligible(mesh) -> bool:
    return (
        mesh.extras.get("layer") not in SKIP_LAYERS
        and mesh.extras.get("material_key") not in SKIP_MATERIALS
        and "component_box" not in mesh.extras
        and mesh.baked_visibility is None
        and len(mesh.positions) > 0
    )


def _lit(meshes):
    return [i for i, mesh in enumerate(meshes) if eligible(mesh)]


def _surfaces(meshes, lit):
    """What the bake reads of each lit mesh: positions, and normals or the indices that derive them."""
    return [
        (mesh.positions, mesh.normals, None if mesh.normals is not None else mesh.indices)
        for mesh in (meshes[i] for i in lit)
    ]


def _ambient_sky(field, surfaces):
    """(N, 2) float32 R/G of every lit vertex, concatenated in mesh order."""
    from turku3d.package.gltf import vertex_normals

    if not surfaces:
        return np.ones((0, 2), np.float32)
    positions = [np.asarray(p, np.float64).reshape(-1, 3) for p, _, _ in surfaces]
    normals = [
        np.asarray(n if n is not None else vertex_normals(p, indices), np.float64).reshape(-1, 3)
        for p, n, indices in surfaces
    ]
    return sample(field, np.concatenate(positions), np.concatenate(normals))[:, :2]


def _attach(meshes, lit, values):
    rgb = np.ones((len(values), 3), np.float32)
    rgb[:, :2] = values
    baked = list(meshes)
    ends = np.cumsum([len(meshes[i].positions) for i in lit])
    for i, part in zip(lit, np.split(rgb, ends[:-1]) if lit else [], strict=True):
        baked[i] = replace(meshes[i], baked_visibility=part)
    return baked


def bake(meshes, field: HeightField):
    """Return ``meshes`` with COLOR_1 on every eligible static surface."""
    lit = _lit(meshes)
    return _attach(meshes, lit, _ambient_sky(field, _surfaces(meshes, lit)))


def stage(cache, tile, meshes, buildings, terrain_heights):
    """Cached bake of the assembled static tile surfaces (``wall_layers``).

    The key holds only what the bake reads and the cached value only the R/G arrays, so
    neither touches the rest of the meshes (images, UVs, extras).
    """
    from pathlib import Path

    from turku3d.package.gltf import vertex_normals
    from turku3d.stage_cache import source_key

    from . import roofs

    lit = _lit(meshes)
    surfaces = _surfaces(meshes, lit)

    def run():
        return _ambient_sky(height_field(tile, buildings, terrain_heights), surfaces)

    values = run() if cache is None else cache.run(
        "visibility",
        source_key(Path(__file__), roofs.plane_height, vertex_normals),
        [tile, surfaces, buildings, np.asarray(terrain_heights)],
        run,
    )
    return _attach(meshes, lit, values)
