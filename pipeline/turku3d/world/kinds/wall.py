"""Walls that hold the ground: quay walls along open water, one entity per 62.5 m cell that has any.

A quay stands where built land (a polygon of the street register) or a mapped wall or quay (OpenStreetMap) reaches the
mapped water outline (`ground.banks.quay_lines`) and lies well above the water: the lidar gives the height of its crest, measured a few metres inland because the scan smears the
drop over the edge. The wall is a closed solid on the land side of the outline, from under the water to the crest.
Green land beside water is a natural bank and stays the ground's slope.

Schema: docs/specs/wall-description.md. UNFINISHED (2026-11-13): no retaining walls away from water (OpenStreetMap
`barrier=retaining_wall`, old `coarse/retaining.py`), no steps down to the water.
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

VERSION = 2
SPACING = 2.0       # m between measured sections along a wall
MIN_HEIGHT = 0.8    # m of crest above the water for a wall to stand
THICKNESS = 0.5     # m: the cap of the wall, on the land side of the outline
ROOT = 1.2          # m under the water level
# Quays: courses of reddish-grey granite about half a metre high under a pale cut-granite coping.
MASONRY = {'family': 'granite', 'params': {'colour': '#8a7f78', 'colour2': '#4d4541', 'finish': 'rock_faced', 'scale_m': 0.55},
           'color': (0.26, 0.23, 0.21), 'roughness': 0.85}
COPING = {'family': 'granite', 'params': {'colour': '#a49d95', 'colour2': '#7d766f', 'finish': 'ashlar', 'scale_m': 1.2},
          'color': (0.37, 0.35, 0.32), 'roughness': 0.7}
COPING_HEIGHT = 0.25   # m of cut stone on top of the courses
COPING_OVERHANG = 0.06  # m proud of the water face
COPING_DEPTH = 0.7     # m from the face inland
COPING_PROUD = 0.03    # m above the ground held behind the wall


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
        return {**envelope(self.name, VERSION, stub, stored, 'w2-street'), **stub.facts, 'masonry': stored.get('masonry', MASONRY),
                'coping': stored.get('coping', COPING)}

    def compile(self, description: dict) -> Output:
        i, j = description['cell']
        e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
        bottom = float(description['level']) - ROOT
        cover = description['masonry']
        coping = description.get('coping', COPING)
        body, cap = _Solid(), _Solid()
        for wall in description['walls']:
            line = np.asarray(wall['line'], float)
            xz = np.column_stack([line[:, 0] - e0, n1 - line[:, 1]])
            inland = np.asarray(wall['inland'], float) * [1.0, -1.0]   # north to engine z
            top = np.asarray(wall['top'], float)
            # The wall's courses up to the coping, and the coping stones: proud of the face and of the ground behind.
            body.prism(xz, inland, top - COPING_HEIGHT, np.full(len(xz), bottom), 0.0, THICKNESS)
            cap.prism(xz, inland, top + COPING_PROUD, top - COPING_HEIGHT, -COPING_OVERHANG, COPING_DEPTH)
        positions_all = np.concatenate([*body.positions, *cap.positions])
        base = float(np.floor(positions_all[:, 1].min()))
        parts = []
        for name, solid, material in (('quay', body, cover), ('quay-coping', cap, coping)):
            positions = np.concatenate(solid.positions)
            positions[:, 1] -= base
            parts.append(Part(name, positions, np.concatenate(solid.triangles),
                              Material(material['family'], material.get('params', {}), tuple(material['color']),
                                       material.get('roughness', 0.8)),
                              uvs=np.concatenate(solid.uvs)))
        return Output((i * CELL, base, j * CELL), parts, error_m={0: 0.05})


class _Solid:
    """Closed prisms along wall lines, as one part's arrays (metre UVs: along the wall, and height)."""

    def __init__(self):
        self.positions, self.triangles, self.uvs = [], [], []
        self.count = 0

    def strip(self, a, b, c, d, u, v0, v1, outward):
        """Quads between two rows of points (a-b along the top edge, d-c along the bottom), facing `outward`."""
        n = len(a)
        block = np.concatenate([a, b, c, d])
        k = np.arange(n) + self.count
        faces = np.concatenate([np.column_stack([k, k + n, k + 2 * n]), np.column_stack([k, k + 2 * n, k + 3 * n])])
        normal = np.cross(block[faces[:, 1] - self.count] - block[faces[:, 0] - self.count],
                          block[faces[:, 2] - self.count] - block[faces[:, 0] - self.count])
        flip = np.einsum('ij,ij->i', normal, np.tile(outward, (2, 1))) < 0
        faces[flip] = faces[flip][:, ::-1]
        self.positions.append(block)
        self.triangles.append(faces)
        self.uvs.append(np.concatenate([np.column_stack([u[0], v0[0]]), np.column_stack([u[1], v0[1]]),
                                        np.column_stack([u[1], v1[1]]), np.column_stack([u[0], v1[0]])]))
        self.count += 4 * n

    def prism(self, xz, inland, top, low, front, depth):
        """A solid from `front` m inland of the line (negative: over the water) to `front + depth`, between `low`
        and `top` (per point)."""
        face, back = xz + inland * front, xz + inland * (front + depth)
        along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(xz, axis=0), axis=1))])
        front_top, back_top = np.column_stack([face[:, 0], top, face[:, 1]]), np.column_stack([back[:, 0], top, back[:, 1]])
        front_low, back_low = np.column_stack([face[:, 0], low, face[:, 1]]), np.column_stack([back[:, 0], low, back[:, 1]])
        out = np.column_stack([-inland[:-1, 0], np.zeros(len(xz) - 1), -inland[:-1, 1]])
        up = np.tile([0.0, 1.0, 0.0], (len(xz) - 1, 1))
        u = (along[:-1], along[1:])
        # Water face, land face, top and underside.
        self.strip(front_top[:-1], front_top[1:], front_low[1:], front_low[:-1], u, (top[:-1], top[1:]), (low[:-1], low[1:]), out)
        self.strip(back_top[:-1], back_top[1:], back_low[1:], back_low[:-1], u, (top[:-1], top[1:]), (low[:-1], low[1:]), -out)
        zero, deep = (np.zeros(len(xz) - 1),) * 2, (np.full(len(xz) - 1, depth),) * 2
        self.strip(front_top[:-1], front_top[1:], back_top[1:], back_top[:-1], u, zero, deep, up)
        self.strip(front_low[:-1], front_low[1:], back_low[1:], back_low[:-1], u, zero, deep, -up)
        # Ends.
        for end, sign in ((0, -1.0), (-1, 1.0)):
            run = (xz[1] - xz[0]) if end == 0 else (xz[-1] - xz[-2])
            run = run / max(np.linalg.norm(run), 1e-9) * sign
            one = lambda p: p[end][None, :]  # noqa: E731
            self.strip(one(front_top), one(back_top), one(back_low), one(front_low), (np.zeros(1), np.full(1, depth)),
                       (top[[end]], top[[end]]), (low[[end]], low[[end]]), np.array([[run[0], 0.0, run[1]]]))
