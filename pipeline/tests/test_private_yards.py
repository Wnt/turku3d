"""Private context stops NPC routing while gates and public paths remain usable."""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.roads import CAR, FOOT
from turku3d.roads.access import private_areas
from turku3d.coarse.fences import place
from turku3d.coarse.fence_survey import profile
from turku3d.lidar import PointCloud


def yard(public=False):
    return [
        (shapely.LineString([(0,19),(0,0),(40,0),(40,40),(0,40),(0,21)]), {"barrier":"fence"}),
        (shapely.Point(0,20), {"barrier":"gate","access":"private"}),
        (shapely.box(10,10,14,14), {"leisure":"garden","access":"private"}),
        *([(shapely.box(0,0,40,40), {"leisure":"park","access":"yes"})] if public else []),
    ]


def test_private_garden_beds_mark_the_enclosing_yard_not_only_the_beds():
    for kind in ("fence","wall","railing"):
        records=yard()
        records[0][1]["barrier"]=kind
        private = private_areas(records)
        assert private.covers(shapely.Point(30,30))
        assert not private.covers(shapely.Point(-1,20))


def test_public_park_remains_public_around_a_private_flowerbed():
    private = private_areas(yard(public=True))
    assert not private.covers(shapely.Point(30,30))
    assert private.covers(shapely.Point(12,12))


def test_enclosed_residential_yard_inherits_privacy_without_marking_the_street_private():
    private = private_areas([
        (shapely.box(0,0,30,30).boundary,{"barrier":"wall"}),
        (shapely.box(-8,0,0,30),{"building":"apartments"}),
    ])
    assert private.covers(shapely.Point(15,15))
    assert not private.covers(shapely.Point(-10,15))


def test_generator_removes_untagged_private_footways_but_keeps_public_segments_and_cars(tmp_path,monkeypatch):
    import producer_world as world
    from turku3d.roads.tile_graph import read_osm

    monkeypatch.setenv("TURKU3D_DATA",str(tmp_path))
    path=world.write_osm([(tags,g) for g,tags in yard()]+[
        ({"highway":"footway"},shapely.LineString([(-10,20),(50,20)])),
        ({"highway":"service"},shapely.LineString([(-10,30),(50,30)])),
    ])
    ways,_,_=read_osm(path,C.VisualTile(0,0,0).bbox)
    foot=[shapely.LineString(w.coords) for w in ways if w.flags&FOOT and not w.flags&CAR]
    assert len(foot)==2
    assert all(g.intersection(shapely.box(1,1,39,39)).length==0 for g in foot)
    assert any(w.flags&CAR for w in ways)


def test_private_gate_is_physically_open_for_players():
    records=[(shapely.LineString([(5,20),(45,20)]),{"barrier":"fence"},False),
             (shapely.Point(25,20),{"barrier":"gate","access":"private"},False)]
    batch=place(C.VisualTile(0,0,0),records,[],lambda x,z:0)
    assert batch
    assert all(abs(s.x-25)>s.length/2+1.4 for s in batch)


def fence_cloud(person=False):
    x,z=np.meshgrid(np.arange(0,41,.25),np.arange(17,23,.25))
    ground=np.c_[x.ravel(),np.ones(x.size),z.ravel()]
    xs=np.arange(5,35.01,.1)
    if person:xs=xs[(xs>15)&(xs<16)]
    tops=np.c_[xs,np.full(len(xs),2.5),np.full(len(xs),20)]
    canopy=tops+[0,8,0]
    cloud=PointCloud.from_xyz(np.r_[ground,tops,canopy])
    cloud.records["classification"][len(ground):]=12
    return cloud


def test_lidar_fence_top_ignores_canopy_and_requires_support_along_the_line():
    line=shapely.LineString([(5,20),(35,20)])
    assert abs(profile(line,fence_cloud())["height"]-1.5)<.1
    assert profile(line,fence_cloud(person=True)) is None


def test_above_grade_lidar_wall_is_not_discarded_as_a_retaining_drop():
    line=shapely.LineString([(10,20),(40,20)])
    sample=lambda x,z: 2 if z>20 else 0
    tile=C.VisualTile(0,0,0)
    assert not place(tile,[(line,{"barrier":"wall"},False)],[],sample)
    assert place(tile,[(line,{"barrier":"wall","height":"1.5","_lidar_height":True},False)],[],sample)


def test_fence_above_a_retaining_edge_starts_at_the_higher_ground():
    x,z=np.meshgrid(np.arange(0,41,.25),np.arange(17,23,.25))
    ground=np.c_[x.ravel(),np.where(z.ravel()<20,8.,4.),z.ravel()]
    xs=np.arange(5,35.01,.1)
    tops=np.c_[xs,np.full(len(xs),9.2),np.full(len(xs),20)]
    cloud=PointCloud.from_xyz(np.r_[ground,tops])
    cloud.records["classification"][len(ground):]=6
    line=shapely.LineString([(5,20),(35,20)])
    measured=profile(line,cloud)
    assert abs(measured["base"]-8)<.01
    assert abs(measured["height"]-1.2)<.1
    batch=place(C.VisualTile(0,0,0),[(line,{"barrier":"fence","height":str(measured['height']),
                                           "_lidar_base":measured['base']},False)],[],lambda x,z:4)
    assert batch and all(abs(s.y-8)<.01 for s in batch)


def test_low_cheek_wall_spans_its_sloping_lower_side_instead_of_a_default_wall():
    from turku3d.coarse.fence_survey import low_profile

    # A 0.3 m wide granite cheek wall: plaza 6.0 m (z < 20), sidewalk 5.6 m
    # (z > 20), coping 6.2 m. Nothing stands 0.8-3.5 m above the plaza.
    x, z = np.meshgrid(np.arange(0, 20.01, .2), np.arange(17, 23.01, .2))
    x, z = x.ravel(), z.ravel()
    keep = abs(z - 20) > .2
    ground = np.c_[x[keep], np.where(z[keep] < 20, 6., 5.6), z[keep]]
    xs, zs = np.meshgrid(np.arange(2, 18.01, .1), np.arange(19.85, 20.16, .1))
    top = np.c_[xs.ravel(), np.full(xs.size, 6.2), zs.ravel()]
    cloud = PointCloud.from_xyz(np.r_[ground, top])
    cloud.records["classification"][len(ground):] = 4
    line = shapely.LineString([(4, 20), (16, 20)])
    assert profile(line, cloud) is None
    measured = low_profile(line, cloud)
    assert measured["low"] and abs(measured["base"] - 5.6) < .01
    assert abs(measured["base"] + measured["height"] - 6.2) < .02
    tags = {"barrier": "wall", "height": str(measured["height"]),
            "_lidar_base": measured["base"], "_lidar_low": True}
    batch = place(C.VisualTile(0, 0, 0), [(line, tags, False)], [], lambda x, z: 5.5)
    assert batch and all(abs(s.y - 5.5) < .01 and abs(s.y + s.height - 6.2) < .02 for s in batch)
    # A tall wall top is not a low wall.
    tall = PointCloud.from_xyz(np.r_[ground, top + [0, 1.6, 0]])
    tall.records["classification"][len(ground):] = 6
    assert low_profile(line, tall) is None
