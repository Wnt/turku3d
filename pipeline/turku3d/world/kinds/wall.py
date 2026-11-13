"""Walls that hold the ground: quay walls along open water, one entity per 62.5 m cell that has any.

A quay stands where built land (a polygon of the street register) reaches the mapped water outline and lies well
above the water: the lidar gives the height of its crest, measured a few metres inland because the scan smears the
drop over the edge. The wall is a closed solid on the land side of the outline, from under the water to the crest.
Green land beside water is a natural bank and stays the ground's slope.

UNFINISHED (2026-11-13): no retaining walls away from water (OpenStreetMap
`barrier=retaining_wall`, old `coarse/retaining.py`), no steps, no coping stones or joints, no schema document.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely
from scipy.ndimage import median_filter

from ..api import Area, Material, Output, Part, Stub, envelope
from ..frame import CELL, ORIGIN_E, ORIGIN_N, cell_at, cell_box_en, cell_key
from ..street import lidar
from .ground.banks import QUAY_REACH, quay_lines
from .ground.mesh import WATER_LEVEL

VERSION = 1
SPACING = 2.0       # m between measured sections along a wall
MIN_HEIGHT = 0.8    # m of crest above the water for a wall to stand
THICKNESS = 0.5     # m: the cap of the wall, on the land side of the outline
ROOT = 1.2          # m under the water level
MASONRY = {'family': 'granite', 'params': {'colour': '#8f8a84', 'colour2': '#5f5b56', 'finish': 'rock_faced', 'scale_m': 0.6},
           'color': (0.27, 0.25, 0.23), 'roughness': 0.8}


def _lines(shape) -> list:
    return [part for part in shapely.get_parts(shapely.line_merge(shape) if shape.geom_type == 'MultiLineString' else shape)
            if part.geom_type == 'LineString' and part.length > SPACING]


class Wall:
    name = 'wall'
    code = (Path(lidar.__file__).parent,)

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(CELL)
        wet, quays = quay_lines(box)
        if wet is None or quays.is_empty:
            return []
        window = lidar.Window(*box)
        (i0, j1), (i1, j0) = cell_at(box[0], box[1]), cell_at(box[2], box[3])
        stubs = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cell = cell_box_en(i, j)
                if not area.touches(cell):
                    continue
                walls = []
                for n, line in enumerate(_lines(quays.intersection(shapely.box(*cell)))):
                    count = max(int(np.ceil(line.length / SPACING)), 1) + 1
                    at = np.linspace(0.0, line.length, count)
                    points = shapely.get_coordinates(shapely.line_interpolate_point(line, at))
                    ahead = shapely.get_coordinates(shapely.line_interpolate_point(line, np.minimum(at + 0.5, line.length)))
                    behind = shapely.get_coordinates(shapely.line_interpolate_point(line, np.maximum(at - 0.5, 0.0)))
                    run = ahead - behind
                    run /= np.maximum(np.linalg.norm(run, axis=1, keepdims=True), 1e-9)
                    normal = np.column_stack([-run[:, 1], run[:, 0]])
                    # Inland is the side that is not water.
                    probe = points + normal * 0.4
                    normal[shapely.contains_xy(wet, probe[:, 0], probe[:, 1])] *= -1
                    crest = window.sample('ground', *(points + normal * (QUAY_REACH + 0.5)).T)
                    # A quay's cap runs level for tens of metres: one sample on a stair, a tree pit or a bridge foot is not it.
                    crest = median_filter(np.nan_to_num(crest, nan=float(np.nanmedian(crest)) if np.isfinite(crest).any() else 0.0), size=7, mode='nearest')
                    if not np.isfinite(crest).all() or np.median(crest) - WATER_LEVEL < MIN_HEIGHT:
                        continue
                    walls.append({'key': f'quay:{cell_key(i, j)}:{n}', 'line': np.round(points, 3).tolist(),
                                  'inland': np.round(normal, 4).tolist(), 'top': np.round(crest, 2).tolist()})
                if walls:
                    refs = [{'source': 'osm', 'key': cell_key(i, j)}, {'source': 'turku-wfs:GIS:Katualueet', 'key': cell_key(i, j)},
                            {'source': 'turku-lidar:pistepilvi', 'key': cell_key(i, j)}]
                    stubs.append(Stub(f'wall/{cell_key(i, j)}', cell, refs, {'cell': [i, j], 'level': WATER_LEVEL, 'walls': walls}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """Source facts; a stored description may set the `masonry` material."""
        stored = stored or {}
        return {**envelope(self.name, VERSION, stub, stored, 'w2-street'), **stub.facts, 'masonry': stored.get('masonry', MASONRY)}

    def compile(self, description: dict) -> Output:
        i, j = description['cell']
        e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
        bottom = float(description['level']) - ROOT
        positions, triangles, uvs = [], [], []
        count = 0

        def strip(a, b, c, d, u, v0, v1, outward):
            """Quads between two rows of points (a-b along the top edge, d-c along the bottom), facing `outward`."""
            nonlocal count
            n = len(a)
            block = np.concatenate([a, b, c, d])
            k = np.arange(n) + count
            faces = np.concatenate([np.column_stack([k, k + n, k + 2 * n]), np.column_stack([k, k + 2 * n, k + 3 * n])])
            normal = np.cross(block[faces[:, 1] - count] - block[faces[:, 0] - count], block[faces[:, 2] - count] - block[faces[:, 0] - count])
            flip = np.einsum('ij,ij->i', normal, np.tile(outward, (2, 1))) < 0
            faces[flip] = faces[flip][:, ::-1]
            positions.append(block)
            triangles.append(faces)
            uvs.append(np.concatenate([np.column_stack([u[0], v0[0]]), np.column_stack([u[1], v0[1]]),
                                       np.column_stack([u[1], v1[1]]), np.column_stack([u[0], v1[0]])]))
            count += 4 * n

        for wall in description['walls']:
            line = np.asarray(wall['line'], float)
            xz = np.column_stack([line[:, 0] - e0, n1 - line[:, 1]])
            inland = np.asarray(wall['inland'], float) * [1.0, -1.0]   # north to engine z
            top = np.asarray(wall['top'], float)
            back = xz + inland * THICKNESS
            along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(xz, axis=0), axis=1))])
            low = np.full(len(xz), bottom)
            front_top, back_top = np.column_stack([xz[:, 0], top, xz[:, 1]]), np.column_stack([back[:, 0], top, back[:, 1]])
            front_low, back_low = np.column_stack([xz[:, 0], low, xz[:, 1]]), np.column_stack([back[:, 0], low, back[:, 1]])
            out = np.column_stack([-inland[:-1, 0], np.zeros(len(xz) - 1), -inland[:-1, 1]])
            up = np.tile([0.0, 1.0, 0.0], (len(xz) - 1, 1))
            u = (along[:-1], along[1:])
            # Water face, land face, cap and underside.
            strip(front_top[:-1], front_top[1:], front_low[1:], front_low[:-1], u, (top[:-1], top[1:]), (low[:-1], low[1:]), out)
            strip(back_top[:-1], back_top[1:], back_low[1:], back_low[:-1], u, (top[:-1], top[1:]), (low[:-1], low[1:]), -out)
            zero, deep = (np.zeros(len(xz) - 1),) * 2, (np.full(len(xz) - 1, THICKNESS),) * 2
            strip(front_top[:-1], front_top[1:], back_top[1:], back_top[:-1], u, zero, deep, up)
            strip(front_low[:-1], front_low[1:], back_low[1:], back_low[:-1], u, zero, deep, -up)
            # Ends.
            for end, sign in ((0, -1.0), (-1, 1.0)):
                run = (xz[1] - xz[0]) if end == 0 else (xz[-1] - xz[-2])
                run = run / max(np.linalg.norm(run), 1e-9) * sign
                one = lambda p: p[end][None, :]
                strip(one(front_top), one(back_top), one(back_low), one(front_low), (np.zeros(1), np.full(1, THICKNESS)),
                      (top[[end]], top[[end]]), (low[[end]], low[[end]]), np.array([[run[0], 0.0, run[1]]]))
        cover = description['masonry']
        positions = np.concatenate(positions)
        base = float(np.floor(positions[:, 1].min()))
        positions[:, 1] -= base
        solid = Part('quay', positions, np.concatenate(triangles),
                     Material(cover['family'], cover.get('params', {}), tuple(cover['color']), cover.get('roughness', 0.8)),
                     uvs=np.concatenate(uvs))
        return Output((i * CELL, base, j * CELL), [solid], error_m={0: 0.05})
