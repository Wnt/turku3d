"""Quay walls: where they stand (mapped walls at the water) and what they are (closed courses under a coping)."""
import geopandas as gpd
import numpy as np
import shapely


def _open_edges(positions: np.ndarray, triangles: np.ndarray) -> int:
    """Edges used by one triangle only, vertices welded by position (each face keeps its own vertices)."""
    _, weld = np.unique(np.round(positions, 5), axis=0, return_inverse=True)
    triangles = weld.ravel()[triangles]
    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum())


def test_a_mapped_wall_at_the_water_is_a_quay_without_a_street_beside_it(monkeypatch):
    from turku3d.world.kinds.ground import banks

    river = shapely.box(0.0, 0.0, 200.0, 40.0)
    wall = shapely.LineString([(20.0, 41.0), (120.0, 41.0)])   # along the north bank, a metre inland
    far = shapely.LineString([(150.0, 60.0), (190.0, 60.0)])    # a retaining wall 20 m from the water: not a quay
    mapped = gpd.GeoDataFrame({'natural': ['water', None, None], 'barrier': [None, 'retaining_wall', 'retaining_wall']},
                              geometry=[river, wall, far])
    monkeypatch.setattr(banks.osm, 'features', lambda box: mapped)
    monkeypatch.setattr(banks.registers, 'layer', lambda name, box: gpd.GeoDataFrame(geometry=[]))
    wet, quays = banks.quay_lines((0.0, 0.0, 200.0, 80.0))
    assert wet.equals(river)
    # The north outline from 16 to 124 m (the wall's reach of 4 m round its ends), nothing else.
    assert abs(quays.length - (100.0 + 2 * banks.WALL_REACH)) < 0.5, quays.length
    assert quays.distance(shapely.Point(170.0, 40.0)) > 10


def test_a_quay_is_closed_courses_under_a_coping():
    from turku3d.world.kinds.wall import COPING_HEIGHT, COPING_PROUD, Wall

    line = [[23459600.0 + 2.0 * k, 6704400.0] for k in range(10)]
    facts = {'cell': [2, 1], 'level': 0.0, 'walls': [{'key': 'quay:c_2_1:0', 'line': line, 'inland': [[0.0, 1.0]] * 10,
                                                       'top': [2.8] * 10}]}
    from turku3d.world.api import Stub

    wall = Wall()
    output = wall.compile(wall.describe(Stub('wall/c_2_1', (0.0, 0.0, 62.5, 62.5), [], facts), None))
    parts = {p.name: p for p in output.parts}
    assert set(parts) == {'quay', 'quay-coping'}
    for part in parts.values():
        assert _open_edges(part.positions, np.asarray(part.triangles)) == 0
    base = output.origin[1]
    courses_top = parts['quay'].positions[:, 1].max() + base
    coping_top = parts['quay-coping'].positions[:, 1].max() + base
    assert abs(courses_top - (2.8 - COPING_HEIGHT)) < 1e-6 and abs(coping_top - (2.8 + COPING_PROUD)) < 1e-6
