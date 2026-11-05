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


def test_ortho_cover_drops_speckle_fills_small_holes_and_traces_straight_edges():
    from turku3d.coarse import surfaces
    tile = C.VisualTile(0, 0, 0)
    asphalt, lawn = [92, 92, 95], [70, 115, 50]
    rgb = np.full((500, 500, 3), asphalt, np.uint8)  # 0.5 m pixels
    rgb[40:44, 40:44] = lawn  # 4 m2 of green on a paved lot: noise
    rgb[100:300, 100:300] = lawn  # 50 m x 50 m lawn ...
    rgb[150:153, 150:153] = asphalt  # ... with a 2.25 m2 shadow/weed gap in it
    yy, xx = np.mgrid[:500, :500]
    rgb[(xx > 320) & (yy > xx + 20)] = lawn  # a diagonal lawn edge (pixel staircase)
    step = tile.size / 500
    classes = surfaces.cover_classes(rgb, step)
    assert classes[42, 42] == mat_id('road') and classes[151, 151] == mat_id('grass')
    regions = surfaces.cover_regions(tile, rgb)
    grass = shapely.union_all([g for g, m in regions if m == mat_id('grass')])
    road = shapely.union_all([g for g, m in regions if m == mat_id('road')])
    assert not grass.intersects(shapely.Point(21, 21)) and grass.covers(shapely.Point(75.7, 75.7))
    assert abs(grass.area + road.area - 250 * 250) < 1e-6 and grass.intersection(road).area < 1e-6
    diagonal = [p for p in shapely.get_parts(grass) if p.bounds[0] > 150][0]
    # One pixel of simplification: a straight diagonal edge, not a 0.5 m staircase of ~170 steps.
    assert len(diagonal.exterior.coords) < 20
    # Per-triangle classification reads the same cleaned classes.
    centroids = np.array([[21.0, 0, 21.0], [75.7, 0, 75.7], [5.0, 0, 5.0]])
    assert surfaces.classify(centroids, rgb, tile, []).tolist() == [mat_id('road'), mat_id('grass'), mat_id('road')]
