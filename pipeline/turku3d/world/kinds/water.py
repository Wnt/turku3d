"""Open water: the surface of the river, the sea and ponds, one entity per 62.5 m cell that has any.

The outline is the mapped water area (OpenStreetMap `natural=water`); the level is the shared N2000 datum of the
tidal river and the sea, as in the old generator (`coarse/water.py`). The ground kind lowers its skin to a bed under
the same outline, so the surface here is a flat sheet with nothing above it; banks, quays and piers are the wall
kinds' and the ground's. UNFINISHED (2026-11-13): no inland levels (ponds lie at their own height), no flow
direction, no schema document yet.
"""

from __future__ import annotations

import numpy as np
import shapely

from ..api import Area, Material, Output, Part, Stub, envelope
from ..frame import CELL, ORIGIN_E, ORIGIN_N, cell_at, cell_box_en, cell_key
from ..street import osm
from .ground.mesh import WATER_LEVEL, facing_up, local_shape

VERSION = 1
SURFACE = {'family': 'water', 'params': {'body': 'river'}, 'color': (0.03, 0.045, 0.04), 'roughness': 0.08}


def _rings(polygon) -> list:
    return [np.asarray(ring.coords).tolist() for ring in (polygon.exterior, *polygon.interiors)]


class Water:
    name = 'water'

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(CELL)
        mapped = osm.features(box)
        if mapped is None:
            return []
        areas = mapped[mapped.geom_type.isin(('Polygon', 'MultiPolygon')) & (mapped.natural == 'water')]
        if not len(areas):
            return []
        bodies = [(shapely.make_valid(row.geometry), f'osm:{row.osm_type}/{row.osm_id}') for row in areas.itertuples()]
        (i0, j1), (i1, j0) = cell_at(box[0], box[1]), cell_at(box[2], box[3])
        stubs = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cell = cell_box_en(i, j)
                if not area.touches(cell):
                    continue
                square = shapely.box(*cell)
                rows = []
                for shape, key in bodies:
                    wet = shape.intersection(square)
                    for part in shapely.get_parts(wet):
                        if part.geom_type == 'Polygon' and part.area > 0.5:
                            rows.append({'key': key, 'rings': _rings(part)})
                if rows:
                    stubs.append(Stub(f'water/{cell_key(i, j)}', cell, [{'source': 'osm', 'key': cell_key(i, j)}],
                                      {'cell': [i, j], 'level': WATER_LEVEL, 'areas': rows}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """Source facts; a stored description may set the `level` and the `surface` material."""
        stored = stored or {}
        return {**envelope(self.name, VERSION, stub, stored, 'w2-street'), **stub.facts,
                'level': stored.get('level', stub.facts['level']), 'surface': stored.get('surface', SURFACE)}

    def compile(self, description: dict) -> Output:
        i, j = description['cell']
        e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
        level = float(description['level'])
        corners = []
        for row in description['areas']:
            cut = shapely.constrained_delaunay_triangles(local_shape(row['rings'], e0, n1))
            parts = shapely.get_parts(cut)
            if len(parts):
                corners.append(shapely.get_coordinates(shapely.get_exterior_ring(parts)).reshape(-1, 4, 2)[:, :3].reshape(-1, 2))
        points, index = np.unique(np.round(np.concatenate(corners), 3), axis=0, return_inverse=True)
        triangles = index.reshape(-1, 3)
        triangles = triangles[(triangles[:, 0] != triangles[:, 1]) & (triangles[:, 1] != triangles[:, 2]) & (triangles[:, 0] != triangles[:, 2])]
        cover = description['surface']
        positions = np.column_stack([points[:, 0], np.zeros(len(points)), points[:, 1]])
        sheet = Part('water', positions, facing_up(points, triangles),
                     Material(cover['family'], cover.get('params', {}), tuple(cover['color']), cover.get('roughness', 0.1)),
                     normals=np.tile([0.0, 1.0, 0.0], (len(points), 1)), uvs=points + np.array([i * CELL, j * CELL]))
        return Output((i * CELL, level, j * CELL), [sheet], error_m={0: 0.01})
