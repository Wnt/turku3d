"""The ground cell on a made-up street whose answer is known."""

import numpy as np

from turku3d.world.frame import CELL, cell_box_en
from turku3d.world.kinds.ground import HALO, STEP, covers
from turku3d.world.kinds.ground.mesh import compile_cell


def street_cell(i=3, j=-2):
    """A cell on a plane sloping east, crossed by a 7 m carriageway 10 cm below its footways, with a marked crossing
    and a centre line."""
    e0, n0, e1, n1 = cell_box_en(i, j)
    side = int(CELL / STEP) + 1 + 2 * HALO
    offsets = (np.arange(side) - HALO) * STEP
    east, north = np.meshgrid(e0 + offsets, n1 - offsets)
    middle = (n0 + n1) / 2
    heights = 10 + 0.02 * (east - e0) - np.where(np.abs(north - middle) < 3.5, 0.1, 0.0)

    def band(south, north_edge, key, level, surface):
        ring = [[e0 - 4, south], [e1 + 4, south], [e1 + 4, north_edge], [e0 - 4, north_edge]]
        return {'key': key, 'source': 'street', 'level': level, 'use': '', 'surface': surface, 'rank': 0 if level == 'low' else 1,
                'rings': [ring], 'cover': covers.of_register(surface, level, False)}

    return {
        'cell': [i, j],
        'relief': {'step': STEP, 'halo': HALO, 'side': side, 'heights_mm': np.rint(heights * 1000).astype(int).ravel().tolist()},
        'surfaces': [band(middle - 3.5, middle + 3.5, 'street:1', 'low', 'Asfalttibetoni'),
                     band(middle + 3.5, middle + 6.5, 'street:2', 'high', 'Betonilaatta'),
                     band(middle - 6.5, middle - 3.5, 'street:3', 'high', 'Betonilaatta')],
        'crossings': [{'key': 'osm:way/1', 'line': [[e0 + 20, middle - 5], [e0 + 20, middle + 5]], 'width': 4.0}],
        'lanes': [{'key': 'osm:way/2', 'line': [[e0 - 4, middle], [e1 + 4, middle]], 'start': 0.0, 'dash': 3.0, 'gap': 9.0}],
        'base': covers.NAMED['unmapped'], 'kerb': covers.NAMED['kerb'],
    }


def test_cell_is_one_height_field_with_kerb_solids():
    output = compile_cell(street_cell())
    skin = [part for part in output.parts if part.lod == 0 and part.name != 'kerbs']
    names = {part.name for part in skin}
    assert {'asphalt-carriageway', 'paving', 'road_paint-carriageway', 'asphalt'} <= names

    # The skin covers the cell exactly once: its plan area is the cell's, and no triangle faces down.
    area = 0.0
    points = []
    for part in skin:
        p = np.asarray(part.positions, dtype=np.float64).reshape(-1, 3)
        t = np.asarray(part.triangles).reshape(-1, 3)
        a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
        signed = 0.5 * ((b[:, 2] - a[:, 2]) * (c[:, 0] - a[:, 0]) - (b[:, 0] - a[:, 0]) * (c[:, 2] - a[:, 2]))
        assert (signed > 0).all()
        area += signed.sum()
        points.append(p[np.unique(t)])
    assert abs(area - CELL * CELL) < 1e-3

    # One height per plan position across parts: the carriageway and what borders it share their edge vertices.
    points = np.round(np.vstack(points), 4)
    plan, index = np.unique(points[:, [0, 2]], axis=0, return_inverse=True)
    top = np.full(len(plan), -np.inf)
    np.maximum.at(top, index.ravel(), points[:, 1])
    bottom = np.full(len(plan), np.inf)
    np.minimum.at(bottom, index.ravel(), points[:, 1])
    assert (top - bottom).max() < 1e-3

    # Kerbs are closed solids: every edge is shared by exactly two triangles.
    kerbs = next(part for part in output.parts if part.name == 'kerbs')
    p = np.round(np.asarray(kerbs.positions, dtype=np.float64).reshape(-1, 3), 4)
    _, welded = np.unique(p, axis=0, return_inverse=True)
    t = welded.ravel()[np.asarray(kerbs.triangles).reshape(-1, 3)]
    edges = np.sort(np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    assert (counts == 2).all()
    assert 0.05 < p[:, 1].max() - p[:, 1].min() < 2.0

    # The raster beside the mesh is the skin: carriageway nodes lie the kerb's reveal below the footway's.
    field = output.heightfield
    assert field.spacing == STEP and np.asarray(field.heights).shape == (126, 126)
    heights = np.asarray(field.heights)
    assert 0.05 < heights[52, 10] - heights[63, 10] < 0.15


def test_a_tile_the_survey_lacks_continues_its_neighbour(tmp_path, monkeypatch):
    """Past the survey's edge the ground runs level from the last measured cells, the same for every window."""
    from turku3d.world.street import lidar

    here, west = '23463500_6707000', '23463000_6707000'
    rows = (20.0 + 0.01 * np.arange(lidar.SIDE, dtype=np.float32))[:, None]
    measured = dict(ground=np.repeat(rows, lidar.SIDE, axis=1), measured=np.ones((lidar.SIDE, lidar.SIDE), bool),
                    canopy=np.zeros((lidar.SIDE, lidar.SIDE), np.uint16))

    def laz_path(name):
        if name != west:
            raise lidar.NotSurveyed(name)
        return tmp_path / 'west.laz'

    monkeypatch.setenv('TURKU3D_WORLD_DATA', str(tmp_path))
    monkeypatch.setattr(lidar, 'laz_path', laz_path)
    monkeypatch.setattr(lidar, 'build', lambda name: measured if name == west else laz_path(name))
    lidar.tile.cache_clear()
    try:
        filled = lidar.tile(here)
        assert not filled['measured'].any() and not filled['canopy'].any()
        assert np.array_equal(filled['ground'][:, 0], measured['ground'][:, -1])
        assert np.array_equal(filled['ground'][:, -1], filled['ground'][:, 0])
        e0, n0 = lidar.tile_corner(here)
        across = lidar.Window(e0 - 20, n0 + 100, e0 + 20, n0 + 101).sample('ground', np.array([e0 - 10.0, e0 + 10.0]), np.full(2, n0 + 100.25))
        assert abs(across[0] - across[1]) < 1e-4
    finally:
        lidar.tile.cache_clear()
