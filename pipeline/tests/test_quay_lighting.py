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
