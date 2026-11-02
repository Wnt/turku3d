"""Pier footprints and heights must not depend on closed area/floating-only tagging."""
import json

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import boats, piers


def test_line_and_polygon_duplicates_keep_area_and_connecting_gangway(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    area = shapely.box(10, 10, 30, 13)
    path = tmp_path / "osm.parquet"
    tags = json.dumps({"man_made": "pier", "floating": "yes"})
    pq.write_table(pa.Table.from_pylist([
        dict(osm_id="area", tags=tags, geometry_local=area.exterior.wkb),
        dict(osm_id="area", tags=tags, geometry_local=area.wkb),
        dict(osm_id="line", tags=tags, geometry_local=shapely.LineString([(0, 11), (25, 11)]).wkb),
        dict(osm_id="fixed", tags=json.dumps({"man_made": "pier", "width": "2 m"}),
             geometry_local=shapely.LineString([(50, 10), (70, 10)]).wkb),
    ]), path)
    records = piers.prepare(C.VisualTile(0, 0, 0), path, [])
    assert len(records) == 3
    assert sum(r['outline'].area for r in records) < 117
    fixed = next(r for r in records if 'fixed' in r['id'])
    assert fixed['outline'].area == 40 and not fixed['floating']
    assert fixed['deck_source'] == 'waterline-fallback'
    meshes = boats.produce(C.VisualTile(0, 0, 0), records)
    assert meshes and all(m.positions[:, 1].max() <= .9 for m in meshes)


def test_deck_uses_spatial_support_and_rejects_water_people_and_quay():
    x,z = np.meshgrid(np.arange(.25,20,.5),np.arange(.25,3,.5))
    p = np.c_[x.ravel(),np.full(x.size,.73),z.ravel()]
    clutter=p[:20].copy();clutter[:,1]=1.3
    water=p.copy();water[:,1]=0
    quay=p.copy();quay[:,0]+=100;quay[:,1]=3
    h, source=piers.deck_height(shapely.box(0,0,20,3),np.concatenate([p,clutter,water,quay]),True)
    assert h==.73 and source=='lidar-deck'
    h,source=piers.deck_height(shapely.box(0,0,20,3),np.empty((0,3)),True)
    assert h==.55 and source=='waterline-fallback'
