"""Geometry regressions matching the observed road/facade failure classes."""
import numpy as np
from turku3d.geometry_qa import Mesh, audit, farm_products


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


def graph():
    return dict(nodes=[0,0,10,0], edges=[[0,1,2,10]], edgeAttrColumns=['width'],
                edgeAttrs=[[6]], edgeHeights=[[0,0]])


def test_cross_slope_uses_lane_direction_and_ignores_stacked_deck():
    a = mesh('road', [[2,0,-1],[2,.4,1],[4,0,-1]])
    r = audit([a],graph=graph())
    assert r['counts'].get('road_cross_slope') == 1
    assert 'road_longitudinal_grade' not in r['counts']
    a.vertices[:,1] += 6
    assert audit([a],graph=graph())['contextual_road_triangles'] == 0


def test_tiny_flipped_codec_dust_is_warning():
    a = mesh('road', [[0,0,0],[.001,0,0],[0,0,.001]])
    r = audit([a])
    assert r['counts'] == {'road_codec_sliver':1}
    assert r['severity'] == {'warning':1}


def test_adjacent_road_height_step():
    a = mesh('a', [[2,0,0],[2,0,1],[4,0,0]])
    b = mesh('b', [[2,.3,0],[2,.3,1],[4,.3,0]])
    r = audit([a,b],graph=graph())
    assert r['counts'].get('road_height_step') == 3


def test_facade_program_bounds_are_not_static_high_severity_overlaps():
    a = mesh('a',[[0,0,0],[0,0,2],[2,0,0]],'facade_kit')
    b = mesh('b',[[0,0,0],[0,0,2],[2,0,0]],'facade_kit')
    a.metadata['vertex_program']='facade-kit'
    r = audit([a,b])
    assert r['counts']=={'coplanar_program_bounds':1}
    assert r['severity']=={'warning':1}


def test_paint_does_not_become_a_drivable_road_triangle():
    a = mesh('paint',[[0,0,0],[2,0,0],[0,0,2]],'road_paint')
    assert audit([a],graph=graph())['counts']=={}


def test_farm_sweep_prioritizes_current_products_then_fallbacks():
    import tempfile,json
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp);current=p/'products/g';fallback=p/'old'
        for directory,tile in [(current,'t0_0_0'),(fallback,'t0_0_0'),(fallback,'t0_1_0')]:
            target=directory/tile;target.mkdir(parents=True,exist_ok=True);(target/'vis.glb').touch()
        (p/'fallbacks.json').write_text(json.dumps([str(fallback)]))
        assert farm_products(p,'g')==[current/'t0_0_0/vis.glb',fallback/'t0_1_0/vis.glb']
