"""Select twenty additional urban quarters in already requested parent drafts."""
import json
import random

import pandas as pd
import shapely
from turku3d import config as C

from gmaps_round3 import PLAN, ROOT, atomic_json


def tags(row):
    value = row.tags
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def select():
    seen = set()
    geometries, records = [], []
    for source in PLAN['sources']:
        for row in pd.read_parquet(source).itertuples():
            key = row.osm_type, row.osm_id
            if key in seen:
                continue
            seen.add(key)
            geometry = shapely.from_wkb(row.geometry)
            if not geometry.is_empty:
                geometries.append(geometry)
                records.append(row)
    tree = shapely.STRtree(geometries)
    used = {row['coverage_id'] for row in PLAN['quarters']}
    candidates = []
    limit = shapely.box(*PLAN['bounds_en'])
    for tile in sorted({row['tile'] for row in PLAN['quarters']}):
        t = C.VisualTile.parse(tile)
        x0, _, z0 = t.origin
        size = t.size / 2
        for qi in (0, 1):
            for qj in (0, 1):
                key = f'{tile}_q{qi}{qj}'
                if key in used:
                    continue
                e, n, _ = C.local_to_en(x0 + qi*size, 0, z0 + qj*size)
                extent = [e, n-size, e+size, n]
                bbox = shapely.box(*extent)
                if not limit.covers(bbox):
                    continue
                buildings, excluded = [], []
                road = 0
                features = set()
                for index in tree.query(bbox, predicate='intersects'):
                    row, geometry = records[index], geometries[index]
                    properties = tags(row)
                    clip = geometry.intersection(bbox)
                    if isinstance(row.building, str) and row.building not in ('', 'no'):
                        buildings.append(clip)
                    if row.natural in ('water', 'wood') or row.landuse in ('forest', 'basin', 'reservoir'):
                        excluded.append(clip)
                    if isinstance(row.highway, str) and row.highway not in ('footway', 'path', 'steps', 'cycleway', 'track'):
                        road += clip.length
                    for feature in ('bridge', 'tunnel'):
                        if properties.get(feature) not in (None, 'no', ''):
                            features.add(feature)
                    if row.railway == 'rail': features.add('rail')
                    if row.leisure in ('park', 'garden'): features.add('park')
                    if properties.get('man_made') == 'quay' or properties.get('waterway') == 'dock': features.add('quay')
                built = shapely.union_all(buildings).area / bbox.area if buildings else 0
                bad = shapely.union_all(excluded).area / bbox.area if excluded else 0
                if bad >= .5 or not (built >= .03 or road >= 80):
                    continue
                candidates.append(dict(coverage_id=key, tile=tile, extent_en=extent,
                    features=sorted(features), building_fraction=built,
                    water_forest_fraction=bad, urban_road_length_m=road))
    random.Random(PLAN['seed']).shuffle(candidates)
    # Keep extra evidence useful for the highest-priority generator themes.
    candidates.sort(key=lambda row: 'bridge' not in row['features'])
    extra = candidates[:20]
    assert len({row['coverage_id'] for row in extra}) == len(extra)
    assert not used.intersection(row['coverage_id'] for row in extra)
    assert abs(shapely.union_all([shapely.box(*row['extent_en']) for row in extra]).area - len(extra)*C.VISUAL_TILE_SIZE**2/4) < 1e-6
    result = dict(seed=PLAN['seed'], source_plan='round2/plan.json',
        selection='Additional urban quarters in existing requested parents; same OSM exclusion thresholds; bridges first, seeded random order.',
        quarters=extra, candidate_count=len(candidates))
    atomic_json(ROOT/'synthetic/extra-plan.json', result)
    atomic_json(ROOT/'synthetic/expanded-plan.json', dict(PLAN, quarters=PLAN['quarters']+extra, extra_selection=result['selection']))
    print(json.dumps(dict(selected=len(extra), candidates=len(candidates))))


if __name__ == '__main__':
    select()
