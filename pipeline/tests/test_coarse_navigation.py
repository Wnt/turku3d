"""Navigation metadata is available before destination draft tiles exist."""
import json

import geopandas as gpd
from shapely.geometry import LineString, Point, box

from turku3d.coarse.navigation import build


def test_routes_to_voiced_street_dropoffs_outside_buildings(tmp_path):
    rows = [
        {'highway': 'residential', 'geometry': LineString([(-100, 187.6), (3300, 187.6)])},
        {'addr:street': 'District', 'addr:housenumber': '1', 'geometry': Point(3000, 200)},
        {'addr:street': 'Blocked', 'addr:housenumber': '2', 'geometry': Point(2900, 200)},
        {'building': 'yes', 'geometry': LineString(box(2895, 188, 2905, 210).exterior.coords)},
        {'addr:street': 'Disconnected', 'addr:housenumber': '3', 'geometry': Point(3000, 400)},
        {'highway': 'footway', 'geometry': LineString([(2800, 400), (3200, 400)])},
    ]
    path = tmp_path / 'osm.parquet'
    frame = gpd.GeoDataFrame([
        dict(osm_type='way', osm_id=i, tags=json.dumps({k: v for k, v in row.items() if k != 'geometry'}),
            geometry_local=row['geometry'], geometry=row['geometry']) for i, row in enumerate(rows)
    ], crs='EPSG:3877')
    frame['geometry_local'] = gpd.GeoSeries(frame.geometry_local, crs=frame.crs)
    frame.to_parquet(path)
    result = build([path], {'district-1', 'blocked-2', 'disconnected-3'})
    addresses = result['addresses']['addresses']
    assert len(addresses) == 1
    assert addresses[0]['id'] == 'district-1'
    assert addresses[0]['car'] > 2500
    assert addresses[0]['point'] == [3000, 191.1]
    assert result['roads']['edges']
    assert build([path], set())['addresses']['addresses'] == []

    landmark = build([path], {'museum'}, [{'name': 'Museum', 'source': ['way', 1]}])['addresses']['addresses']
    assert len(landmark) == 1
    assert landmark[0]['address'] == 'Museum'
    assert landmark[0]['landmark'] is True
    assert landmark[0]['point'] == [3000, 191.1]
