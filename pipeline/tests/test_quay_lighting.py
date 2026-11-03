from types import SimpleNamespace
import numpy as np
import shapely
from turku3d import config as C
from turku3d.col.materials import mat_id
import json
from turku3d.coarse.quay_lighting import OBSERVATIONS, extent, visuals


def bank(height=2):
    return SimpleNamespace(positions=np.array([[0.,height,0],[10.,height,0],[0.,-3,0],[10.,-3,0]]),
                           indices=np.array([[0,1,2],[1,3,2]],np.uint32),material=np.array([mat_id('quay')]*2))


def test_measured_lower_crest_has_strip_and_falling_wash_with_same_wall():
    surface=bank(); before=surface.positions.copy()
    walk=dict(line=shapely.LineString([(0,2),(10,2)]),width=3)
    meshes=visuals(C.VisualTile(0,0,0),surface,[walk],[shapely.box(-1,-1,11,4)],0)
    wall=next(m for m in meshes if m.extras['material_key']=='draft_quay_stone_lit')
    bar=next(m for m in meshes if m.extras['material_key']=='night_quay_bar')
    assert len(bar.indices)==2 and np.isclose(np.ptp(bar.positions[:,1]),.045)
    assert np.all(wall.positions[:,2]==0) and wall.positions[:,1].min()==-3
    assert wall.colors is None  # COLOR_0 would become far-scenery pigment
    assert np.all(wall.night_irradiance[wall.positions[:,1]<=1.1]==0)
    tint=np.asarray(json.loads(OBSERVATIONS.read_text())['linear_rgb'])
    assert np.allclose(wall.night_irradiance[wall.positions[:,1]==2],tint)
    assert np.allclose(bar.emissive,tint)
    bar_normal=np.cross(*(bar.positions[bar.indices[0]][1:]-bar.positions[bar.indices[0]][0]))
    assert bar_normal[2]<0  # bar faces the water side, as the wall does
    assert np.allclose(surface.positions,before)  # collision mesh never changes
    assert np.all(wall.normals[:,2]<0)  # original water-facing winding


def test_no_lights_on_high_bank_unmeasured_walk_or_unlit_corridor():
    walk=dict(line=shapely.LineString([(0,2),(10,2)]),width=3)
    for surface,walks,paths in [(bank(4),[walk],[shapely.box(-1,-1,11,4)]),
                               (bank(),[],[shapely.box(-1,-1,11,4)]),(bank(),[walk],[])]:
        meshes=visuals(C.VisualTile(0,0,0),surface,walks,paths,0)
        assert all('light' not in m.name and not m.name.startswith('quay-lit') for m in meshes)


def test_extent_follows_named_mapped_bridges_and_curved_river_without_coordinates_in_rule():
    records=[({'waterway':'river','name':'River'},shapely.LineString([(0,0),(50,20),(100,0)])),
             ({'bridge':'yes','name':'Start'},shapely.LineString([(0,-10),(0,10)])),
             ({'bridge':'yes','name':'End'},shapely.LineString([(100,-10),(100,10)]))]
    policy=dict(waterway='River',between_bridges=['Start','End'])
    mask=extent(records,policy)
    assert mask.covers(shapely.Point(50,20))
    assert not mask.covers(shapely.Point(-40,0))
    assert extent(records[:2],policy).is_empty


def test_t0_0_1_cut_residue_and_malformed_wash_never_fail_the_tile(tmp_path):
    # Farm t0_0_1 (pin vfix-243e981f): a facade cut through the zero-wash row of the lit
    # quay wall left -1.1e-18 irradiance and build_glb rejected the whole tile.
    from turku3d.package.gltf import GlbMesh, build_glb
    from turku3d.wall_surfaces import layer_meshes
    positions = np.array([[0, 1e-4, .0032], [.0054, .0859, 0], [.0054, 0, 0]])
    night = np.array([[-1.0975871309839974e-18, -8.122144537444882e-19, -4.829383227291711e-19],
                      [.04239926507269429, .03137545525821971, .018655676056259306], [np.nan, 0, 0]])
    extras = dict(layer='ground', material_key='draft_quay_stone_lit', object_id='quay-lit:t0_0_1')
    mesh = GlbMesh('quay-lit-stone', positions, np.array([[0, 1, 2]], np.uint32), night_irradiance=night, extras=extras)
    path = tmp_path / 'ground.vis.glb'; path.write_bytes(build_glb([mesh]))
    loaded = layer_meshes([path])[0].night_irradiance
    assert np.isfinite(loaded).all() and loaded.min() == 0
    np.testing.assert_allclose(loaded[1], night[1], rtol=1e-6)
    malformed = GlbMesh('quay-lit-stone', positions, np.array([[0, 1, 2]], np.uint32), night_irradiance=night[:2], extras=extras)
    path.write_bytes(build_glb([malformed]))  # dropped (renders unlit), not a failed tile
    assert layer_meshes([path])[0].night_irradiance is None


def test_wall_cut_keeps_quay_wash_non_negative():
    from turku3d.wall_assembly import assemble_structures
    from turku3d.package.gltf import GlbMesh
    wall = GlbMesh('quay-lit-stone', np.array([[0., 0, 0], [10, 0, 0], [0, 2, 0], [10, 2, 0]]),
                   np.array([[0, 1, 2], [1, 3, 2]], np.uint32),
                   night_irradiance=np.array([[0., 0, 0], [0, 0, 0], [1, .74, .44], [1, .74, .44]]),
                   extras=dict(layer='ground', material_key='draft_quay_stone_lit', object_id='quay-lit:t'))
    facade = GlbMesh('facade', np.array([[3., 0, -.01], [3.7, 0, -.01], [3, 1.3, -.01], [3.7, 1.3, -.01]]),
                     np.array([[0, 1, 2], [1, 3, 2]], np.uint32),
                     extras=dict(layer='buildings', material_key='facade_kit', object_id='b'))
    for mesh in assemble_structures([facade, wall]):
        if mesh.night_irradiance is not None:
            assert mesh.night_irradiance.min() >= 0 and np.isfinite(mesh.night_irradiance).all()
