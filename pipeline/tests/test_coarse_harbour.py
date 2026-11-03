"""Harbour source semantics, vertical measurements and bounded lattice output."""
import json

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import boat_inputs, boats, harbour


def test_ship_and_pier_shells_cannot_seed_land_terraces():
    records = [dict(building=key) for key in ('house', 'ship', 'pier')]
    vessels = [dict(building_keys=['ship']), dict(building_keys=['pier'])]
    assert boat_inputs.separate_attachments(records, vessels) == [records[0]]


def test_historic_ship_is_recognised_without_building_tag(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    osm, water = tmp_path / 'osm.parquet', tmp_path / 'water.parquet'
    pq.write_table(pa.Table.from_pylist([dict(osm_id='museum', tags=json.dumps({'historic':'ship'}),
                                           geometry_local=shapely.box(10,10,50,18).wkb)]), osm)
    pq.write_table(pa.Table.from_pylist([dict(geometry_local=shapely.box(0,0,100,100).wkb)]), water)
    records = boat_inputs.prepare(C.VisualTile(0,0,0), osm, None, water)
    assert len(records) == 1 and records[0]['kind'] == 'ship'


def test_lidar_ship_resolves_separate_tall_masts_and_dense_cabin():
    deck = np.array([[x, 2., z] for x in np.arange(2,58,.5) for z in np.arange(2,10,.5)])
    cabin = np.array([[x, 6., z] for x in np.arange(25,35,.2) for z in np.arange(3,9,.2)])
    masts = np.array([[x, y, 6.] for x in (12.,43.) for y in np.arange(2,30,.2)])
    # Vegetation above the hull must not contribute false tall masts.
    tree = np.array([[20., y, 5.] for y in np.arange(2,45,.1)])
    points = np.concatenate([deck,cabin,masts,tree])
    classes = np.r_[np.ones(len(deck)+len(cabin)+len(masts)), np.full(len(tree),5)]
    boat = dict(outline=shapely.box(0,0,60,12), deck=1.5, cabins=[],masts=[])
    harbour.survey_ship(boat,points,classes)
    assert boat['deck'] == 2.
    assert len(boat['masts']) == 2
    assert all(29 < top < 31 for _,top in boat['masts'])
    assert len(boat['cabins']) == 1
    assert 50 < boat['cabins'][0][0].area < 80 and boat['cabins'][0][1] == 6


def test_crane_and_mast_are_not_house_or_ship_and_clip_across_seams(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    osm = tmp_path/'osm.parquet'
    pq.write_table(pa.Table.from_pylist([
        dict(osm_id='crane',tags=json.dumps({'man_made':'crane','height':'30'}),
             geometry_local=shapely.Point(249,120).wkb),
        dict(osm_id='mast',tags=json.dumps({'man_made':'mast','height':'18'}),
             geometry_local=shapely.Point(240,110).wkb),
        dict(osm_id='church',tags=json.dumps({'man_made':'tower','tower:type':'bell_tower'}),
             geometry_local=shapely.Point(200,110).wkb),
    ]), osm)
    records = harbour.prepare(C.VisualTile(0,0,0),osm,[])
    assert {r['kind'] for r in records} == {'crane','mast'}
    totals = []
    for tile in (C.VisualTile(0,0,0),C.VisualTile(0,1,0)):
        meshes = boats.produce(tile,records)
        assert meshes and all(np.isfinite(m.positions).all() for m in meshes)
        assert all(not m.metadata['collision'] for m in meshes)
        assert all(m.positions[:,0].min() >= tile.bbox.xmin and
                   m.positions[:,0].max() <= tile.bbox.xmax for m in meshes)
        assert all(m.extras['layer']=='structures' for m in boats.visuals(tile,meshes))
        totals.append(sum(len(m.indices) for m in meshes))
    assert 0 < sum(totals) < 5000


def test_lidar_ship_measures_sheer_funnels_and_lifeboats_into_livery_parts():
    def grid(x0, x1, z0, z1, y, step=.4):
        return np.array([[x, y, z] for x in np.arange(x0, x1, step) for z in np.arange(z0, z1, step)])
    # Main deck at 6 m rising to 7.5 m over the bow (+x), a long upper tier at 9 m,
    # two tall oval funnels and four small boats along both deck edges.
    # City lidar is dense (~50 returns/m2): every 0.5 m cell holds several returns.
    deck = np.array([[x, 6 + max(0, x - 70) * .15, z] for x in np.arange(1, 80, .25) for z in np.arange(1, 15, .25)])
    tier = grid(20, 60, 1, 15, 9., .25)
    funnels = [np.array([[x, 15., z] for x in np.arange(c - 2.5, c + 2.5, .25) for z in np.arange(6, 10, .25)
                         if ((x - c) / 2.5) ** 2 + ((z - 8) / 2) ** 2 <= 1]) for c in (32., 44.)]
    lifeboats = [grid(x, x + 7, z, z + 2.2, 10.6, .25) for x in (22., 50.) for z in (2.2, 11.6)]
    points = np.concatenate([deck, tier, *funnels, *lifeboats])
    boat = dict(outline=shapely.Polygon([(0, 0), (72, 0), (80, 8), (72, 16), (0, 16)]),
                deck=1.5, cabins=[], masts=[], water=.2, tags={})
    harbour.survey_ship(boat, points, np.ones(len(points)))
    assert boat['bow'] == 1 and abs(boat['deck'] - 6) < .2
    heights = dict(boat['sheer'])
    assert max(heights.values()) > boat['deck'] + .8
    assert len(boat['funnels']) == 2 and all(14 < f['top'] < 16 for f in boat['funnels'])
    assert len(boat['lifeboats']) == 4
    assert not any(abs(top - 15) < 1 for _, top in boat['cabins'])
    boat.update(id='t', kind='ship', building_keys=[], source_facades=[], owner='t0_0_0',
                appearance=dict(cabin_surface='white', hull_surface='paint_9c8a6d', stem_rake=.5,
                                funnel_bands=[[.2, 'paint_2c3238'], [1, 'paint_b69a81']],
                                lifeboat_surface='paint_b8583d', porthole_rows=[1.2], dress_lights=True))
    meshes = boats.produce(C.VisualTile(0, 0, 0), [boat])
    surfaces = {m.metadata['surface'] for m in meshes}
    assert {'paint_9c8a6d', 'paint_2c3238', 'paint_b69a81', 'paint_b8583d', 'glass'} <= surfaces
    hull = np.concatenate([m.positions for m in meshes if m.metadata['surface'] == 'paint_9c8a6d'])
    # The stem rakes: the keel stops short of the deck-level bow.
    assert hull[hull[:, 1] < 1, 0].max() < hull[hull[:, 1] > 6, 0].max() - 1.5
    assert all(np.isfinite(m.positions).all() for m in meshes)
