"""Geometry regressions matching the observed road/facade failure classes."""
import numpy as np
from turku3d.geometry_qa import Mesh, audit


def mesh(name, points, material='draft_asphalt'):
    return Mesh(name, np.asarray(points, float), np.arange(len(points)).reshape(-1, 3),
                dict(material_key=material))


def test_overlap_different_triangulation_and_vertical_facade():
    a = mesh('ground', [[0,0,0],[0,0,2],[2,0,0]])
    b = mesh('road', [[.2,.001,.2],[.2,.001,1],[1,.001,.2]])
    assert audit([a,b])['counts'] == {'coplanar_overlap': 1}
    for m in [a,b]:
        m.vertices = m.vertices[:, [0,2,1]]
        m.metadata = dict(material_key='facade')
    assert audit([a,b])['counts'] == {'coplanar_overlap': 1}


def test_adjacent_triangles_and_offset_overlay_are_clean():
    a = mesh('ground', [[0,0,0],[0,0,2],[2,0,0]])
    b = mesh('road', [[2,0,2],[2,0,0],[0,0,2]])
    c = mesh('overlay', [[0,.02,0],[0,.02,2],[2,.02,0]])
    assert audit([a,b,c])['counts'] == {}


def test_spike_winding_grade_and_sliver():
    spike = mesh('road', [[0,0,0],[0,0,2],[2,-9993,0]])
    assert audit([spike])['counts']['height_spike'] == 1
    flipped = mesh('road', [[0,0,0],[2,0,0],[0,0,2]])
    assert audit([flipped])['counts'] == {'road_flipped': 1}
    steep = mesh('road', [[0,0,0],[0,1,2],[2,0,0]])
    assert audit([steep])['counts'] == {'road_grade': 1}
    thin = mesh('road', [[0,0,0],[0,0,.001],[10,0,0]])
    assert audit([thin])['counts'] == {'road_sliver': 1}


def test_report_position_is_epsg3877():
    a = mesh('road', [[500,0,-308],[502,0,-308],[500,0,-306]])
    f = audit([a])['findings'][0]
    assert abs(f['world_enh'][0] - 23460000.6666667) < .001
    assert abs(f['world_enh'][1] - 6704807.3333333) < .001
