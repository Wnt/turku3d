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

Work is bounded: ``AZIMUTHS`` × ``STEPS`` height lookups and a fixed elevation table per
vertex, vectorised in chunks; no per-triangle traversal and no native dependency. The
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
CHUNK = 16384
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
_PHI = (np.arange(AZIMUTHS) + 0.5) * 2 * np.pi / AZIMUTHS
_DIRECTIONS = np.stack([np.cos(_PHI), np.sin(_PHI)], axis=1)  # (K, 2) x/z


def _hemisphere(horizontal, up):
    """Per-bin cosine-weighted solid angle (N, K, E) of the normal's hemisphere.

    ``horizontal`` (N, K) is the normal's component along each azimuth, ``up`` (N,) its y.
    """
    cos_t, sin_t = np.cos(_THETA), np.sin(_THETA)
    weight = horizontal[:, :, None] * cos_t + up[:, None, None] * sin_t
    return np.maximum(weight, 0) * cos_t * (np.pi / ELEVATIONS)


def _above(bins, horizon):
    """Sum of the bins whose elevation centre lies above ``horizon`` (N, K)."""
    return np.where(_THETA[None, None, :] > horizon[:, :, None], bins, 0).sum(axis=2)


def sample(field: HeightField, positions, normals):
    """(N,3) float32 visibility for tile-frame ``positions`` with unit ``normals``."""
    positions = np.asarray(positions, np.float64)
    normals = np.asarray(normals, np.float64)
    result = np.ones((len(positions), 3), np.float32)
    for begin in range(0, len(positions), CHUNK):
        p, n = positions[begin:begin + CHUNK], normals[begin:begin + CHUNK]
        length = np.linalg.norm(n, axis=1, keepdims=True)
        n = np.where(length > 1e-6, n / np.maximum(length, 1e-9), [0.0, 1.0, 0.0])
        flat = np.hypot(n[:, 0], n[:, 2])
        lateral = np.where(flat[:, None] > 1e-3, n[:, [0, 2]] / np.maximum(flat, 1e-9)[:, None], 0)
        start = p[:, [0, 2]] + lateral * (WALL_OFFSET * flat)[:, None]
        ground = field.sample(field.terrain, start[:, 0], start[:, 1])
        height = np.maximum(p[:, 1] + 0.1 * np.maximum(n[:, 1], 0), ground + GROUND_CLEARANCE)
        # (N, K, S) occluder elevations along each azimuth.
        x = start[:, 0, None, None] + _DIRECTIONS[None, :, 0, None] * STEPS[None, None, :]
        z = start[:, 1, None, None] + _DIRECTIONS[None, :, 1, None] * STEPS[None, None, :]
        rise = field.sample(field.heights, x, z) - height[:, None, None]
        elevation = np.arctan2(rise, STEPS[None, None, :])
        near = elevation[:, :, :STEPS_LOCAL].max(axis=2)
        far = elevation.max(axis=2)
        horizontal = lateral @ _DIRECTIONS.T * flat[:, None]
        bins = _hemisphere(horizontal, n[:, 1])
        whole = bins.sum(axis=(1, 2))
        local = _above(bins, near).sum(axis=1)
        open_sky = _above(bins, np.zeros_like(far)).sum(axis=1)
        sky = _above(bins, np.maximum(far, 0)).sum(axis=1)
        out = result[begin:begin + CHUNK]
        out[:, 0] = np.where(whole > 1e-6, local / np.maximum(whole, 1e-9), 1)
        # A face that sees no open sky (downward) keeps its ambient; R still records its creases.
        out[:, 1] = np.where(open_sky > 1e-6, sky / np.maximum(open_sky, 1e-9), 1)
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


def bake(meshes, field: HeightField):
    """Return ``meshes`` with COLOR_1 on every eligible static surface."""
    from turku3d.package.gltf import vertex_normals

    baked = []
    for mesh in meshes:
        if not eligible(mesh):
            baked.append(mesh)
            continue
        normals = mesh.normals if mesh.normals is not None else vertex_normals(mesh.positions, mesh.indices)
        baked.append(replace(mesh, baked_visibility=sample(field, mesh.positions, normals)))
    return baked


def stage(cache, tile, meshes, buildings, terrain_heights):
    """Cached bake of the assembled static tile surfaces (``wall_layers``)."""
    from pathlib import Path

    from turku3d.package.gltf import vertex_normals
    from turku3d.stage_cache import source_key

    from . import roofs

    def run():
        return bake(meshes, height_field(tile, buildings, terrain_heights))

    if cache is None:
        return run()
    return cache.run(
        "visibility",
        source_key(Path(__file__), roofs.plane_height, vertex_normals),
        [tile, meshes, buildings, np.asarray(terrain_heights)],
        run,
    )
