import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.coarse import fences, hedges, garden_finishes
from turku3d.coarse.street_materials import BRICK


def test_mapped_hedge_survives_parallel_path_but_opens_at_crossing_and_gate():
    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(10, 10), (40, 10)])
    paths = [(shapely.LineString([(10, 11), (40, 11)]), 3),
             (shapely.LineString([(25, 0), (25, 20)]), 2)]
    cuts = hedges.crossing_mask(line, paths)
    tags = dict(barrier='hedge', _hedge_openings=cuts, _hedge_height=3, _hedge_width=2)
    gate = (shapely.Point(15, 10), dict(barrier='gate'), True)
    roads = [(p.buffer(width / 2), mat_id('road')) for p, width in paths]
    batch = fences.place(tile, [(line, tags, False), gate], roads, lambda x,z: 5)
    assert abs(sum(s.length for s in batch) - 24.7) < .01
    assert all(s.height == 3 and s.width == 2 for s in batch)
    assert all(not (13.5 < s.x < 16.5 or 23.85 < s.x < 26.15) for s in batch)
    collision = [p for ct in tile.collision_tiles() for p in fences.collision(batch, ct)]
    assert all(p.params[2] == 1 for p in collision)


def test_flat_clipped_hedge_measurement_rejects_disconnected_tree_returns():
    x,z = np.meshgrid(np.arange(10,30,.2), np.arange(9,11,.2))
    ground = np.c_[x.ravel(), np.full(x.size,5), z.ravel()]
    top = ground + [0,3,0]
    line = shapely.LineString([(10,10),(30,10)])
    p = np.concatenate([ground,top]); classes=np.r_[np.full(len(ground),2),np.full(len(top),4)]
    h,w,top_level,shift = hedges.dimensions(line,p,classes)
    assert h == 3 and top_level == 8 and 1 <= w <= 1.4 and abs(shift) < .2
    top[:,1] += np.linspace(0,2,len(top))
    assert hedges.dimensions(line,np.concatenate([ground,top[::100]]),
        np.r_[np.full(len(ground),2),np.full(len(top[::100]),4)]) is None


def test_brick_finish_only_changes_supported_paving_not_nearby_lawn():
    tile = C.VisualTile(0,0,0)
    rgb = np.full((512,512,3), [160,100,80], dtype='uint8')
    paving=shapely.box(20,20,30,50); garden=shapely.box(30,20,50,50)
    result=garden_finishes.regions(tile,rgb,[(paving,mat_id('paving_slab')),(garden,mat_id('grass'))],[garden])
    assert result and all(m==BRICK for _,m in result)
    union=shapely.union_all([g for g,_ in result])
    assert union.symmetric_difference(paving).area < .01
    assert union.intersection(garden).area == 0


def test_brick_paving_has_finite_periodic_pbr_maps():
    from turku3d.coarse.ground_finishes import maps
    result = maps('brick_paving', 128)
    for pixels in result.values():
        assert pixels.shape == (128,128,3) and pixels.dtype == np.uint8
        edge=np.abs(pixels[:,0].astype(float)-pixels[:,-1]).mean()
        interior=np.abs(np.diff(pixels.astype(float),axis=1)).mean()
        assert edge < max(3 * interior, 8)
    assert result['normal'][...,2].min() > 128
    assert np.all(result['orm'][...,2] == 0)


def test_surveyed_hedge_top_stays_flat_over_uneven_terrain():
    tile = C.VisualTile(0,0,0)
    line = shapely.LineString([(10,10),(30,10)])
    tags = dict(barrier='hedge',_hedge_height=3,_hedge_width=2,_hedge_top=8)
    batch = fences.place(tile,[(line,tags,False)],[],lambda x,z: 4+x*.03)
    assert batch and all(abs(s.y+s.height-8)<1e-6 for s in batch)
    explicit = fences.place(tile,[(line,dict(tags,height='1'),False)],[],lambda x,z:5)
    assert all(s.height==1 for s in explicit)


def test_hedge_on_a_hill_keeps_measured_height_without_false_horizontal_crown():
    x,z = np.meshgrid(np.arange(10,30,.2),np.arange(9,11,.2))
    ground=np.c_[x.ravel(),5+(x.ravel()-10)*.2,z.ravel()]
    points=np.concatenate([ground,ground+[0,3,0]])
    classes=np.r_[np.full(len(ground),2),np.full(len(ground),4)]
    height,width,level,shift=hedges.dimensions(shapely.LineString([(10,10),(30,10)]),points,classes)
    assert height==3 and 1<=width<=1.4 and level is None


def test_offset_mapping_does_not_double_hedge_width():
    x,z=np.meshgrid(np.arange(10,30,.2),np.arange(10.5,11.5,.05))
    ground=np.c_[x.ravel(),np.full(x.size,5),z.ravel()]
    p=np.concatenate([ground,ground+[0,3,0]])
    c=np.r_[np.full(len(ground),2),np.full(len(ground),4)]
    h,w,top,shift=hedges.dimensions(shapely.LineString([(10,10),(30,10)]),p,c)
    assert h==3 and top==8 and .5<=w<=.7 and .8<shift<1.1
