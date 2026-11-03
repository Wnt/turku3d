"""Data-driven residential detail preserves small lawns without painting roofs or roads."""
import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import yard_cover
from turku3d.coarse.surfaces import split_boundaries
from turku3d.col.materials import mat_id


def test_lawn_islands_split_coarse_triangles_and_respect_buildings_and_street_priority():
    tile=C.VisualTile(0,0,0)
    rgb=np.full((250,250,3),120,np.uint8)
    rgb[10:15,10:12]=[70,115,50]
    rgb[20:25,20:22]=[25,45,48]
    rgb[30:35,30:32]=[70,115,50]
    domain=shapely.box(0,0,100,100)
    regions=yard_cover.regions(tile,rgb,domain,[shapely.box(29,29,34,36)])
    assert len(regions)==2
    assert all(r[0].intersection(shapely.box(29,29,34,36)).is_empty for r in regions)
    assert abs(sum(r[0].area for r in regions)-20)<1e-8
    # Both strips fall inside a triangle whose centroid sees neutral pavement.
    p=np.array([[0,2,0],[100,2,0],[0,2,100]],float)
    ix=np.array([[0,2,1]],np.uint32)
    street=shapely.box(9,10,11,15)
    v,tri,ids,_=split_boundaries(p,ix,np.array([mat_id('road')],np.uint8),
        np.array([True]),[*regions,(street,mat_id('road'))])
    area=abs(np.cross(v[tri][:,1]-v[tri][:,0],v[tri][:,2]-v[tri][:,0])[:,1])/2
    assert abs(sum(area[ids==mat_id('grass')])-15)<1e-8
    assert np.all(v[:,1]==2)


def test_shadow_asphalt_stays_neutral_and_tiny_isolated_green_returns_are_dropped():
    rgb=np.array([[[25,45,48],[31,33,40],[0,0,0],[255,255,255]]],np.uint8)
    assert yard_cover.lawn_mask(rgb).tolist()==[[True,False,False,False]]
    tile=C.VisualTile(0,0,0);rgb=np.full((500,500,3),120,np.uint8);rgb[10,10]=[20,50,20]
    assert yard_cover.regions(tile,rgb,shapely.box(0,0,250,250),[])==[]


def test_bright_courtyard_paving_keeps_explicit_road_and_grass_priority():
    assert not yard_cover.paving_mask(np.full((1,1,3),255,np.uint8)).any()
    tile = C.VisualTile(0, 0, 0)
    rgb = np.full((250, 250, 3), [75, 77, 78], np.uint8)
    rgb[10:20, 10:20] = [176, 174, 164]
    rgb[12:15, 12:15] = [100, 136, 77]
    rgb[20:25, 20:25] = [163, 97, 82]  # Coloured play surfacing is not neutral stone.
    regions = yard_cover.regions(tile, rgb, shapely.box(0, 0, 50, 50), [])
    paving = shapely.union_all([g for g,m in regions if m == mat_id('paving_slab')])
    grass = shapely.union_all([g for g,m in regions if m == mat_id('grass')])
    assert abs(paving.area - 91) < 1e-8 and abs(grass.area - 9) < 1e-8
    assert paving.intersection(grass).area == 0
    assert not paving.covers(shapely.Point(22, 22))
    p = np.array([[0,2,0],[50,2,0],[0,2,50]],float)
    _, _, ids, _ = split_boundaries(p, np.array([[0,2,1]],np.uint32),
        np.array([mat_id('road')],np.uint8),np.array([True]),
        [*regions,(shapely.box(0,0,50,50),mat_id('road'))])
    assert np.all(ids == mat_id('road'))
