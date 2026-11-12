"""Freeze bounded, measured hedge crowns and mapped path openings as local inputs."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import shapely

VERSION = 'turku-hedge-survey/1'


def freeze(output, bounds, osm):
    from turku3d import config as C
    from turku3d.spatial import rows
    from turku3d.ground.inputs import laz_paths, read_returns
    from turku3d.coarse.hedges import dimensions, crossing_mask

    window = shapely.box(*bounds)
    mapped, paths = [], []
    for _, row in rows(osm, C.BBoxLocal(*bounds).buffered(50)):
        tags = json.loads(row['tags'] or '{}')
        geometry = shapely.force_2d(row['geometry_local'])
        if tags.get('barrier') == 'hedge' and geometry.intersects(window):
            for part, line in enumerate(shapely.get_parts(geometry)):
                if line.geom_type == 'LineString':
                    mapped.append((f"osm:{row['osm_type']}:{row['osm_id']}:{part}", line))
        if not tags.get('highway') or geometry.geom_type not in ('LineString', 'MultiLineString'):
            continue
        if tags.get('bridge', 'no') != 'no' or tags.get('tunnel', 'no') != 'no' or tags.get('layer', '0') not in ('0', ''):
            continue
        try:
            width = float(tags.get('width', 2.5 if tags['highway'] in ('footway', 'path', 'steps') else 6.))
        except ValueError:
            width = 2.5
        paths.append((geometry, float(np.clip(width, .3, 15))))
    if not mapped:
        raise ValueError('No mapped hedges in bounded survey')
    source_box = C.BBoxLocal(*shapely.union_all([g for _, g in mapped]).buffer(2.2).bounds)
    sources = laz_paths(source_box)
    if {p.stem for p in sources} != {tile.name for tile in C.laz_tiles_for_bbox(source_box)}:
        raise ValueError('Incomplete raw lidar coverage for mapped hedges')
    points, classes = read_returns(sources, source_box, [2, 3, 4, 5])
    network_bounds = shapely.bounds([g for g, _ in paths]).reshape(-1, 4)
    records, rejected = [], []
    for identity, line in sorted(mapped, key=lambda item: item[0]):
        x, z, X, Z = line.buffer(2.2).bounds
        selected = (points[:, 0] >= x) & (points[:, 0] <= X) & (points[:, 2] >= z) & (points[:, 2] <= Z)
        measured = dimensions(line, points[selected], classes[selected])
        if measured is None:
            rejected.append(dict(id=identity, reason='insufficient coherent clipped-hedge returns'))
            continue
        height, width, top, shift = measured
        shifted = line.offset_curve(shift, join_style='mitre')
        if shifted.is_empty or shifted.geom_type != 'LineString':
            raise ValueError('Measured hedge centreline became invalid')
        openings = crossing_mask(shifted, paths, network_bounds)
        records.append(dict(id=identity, line=shapely.to_geojson(shifted), openings=shapely.to_geojson(openings),
                            widthM=width, heightM=height, worldTopM=top, centreShiftM=shift,
                            uncertaintyM=.15, returns=int(selected.sum())))
    result = dict(version=VERSION, bounds=list(bounds), heightDatum='N2000', records=records, rejected=rejected,
                  sources=[dict(name=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in [Path(osm), *sources]])
    output = Path(output);output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n')
    return result


def load(cache, bounds):
    path = Path(cache)/'hedge-survey.json'
    if not path.exists():
        return [], []
    raw = path.read_bytes();data = json.loads(raw)
    covered = data['bounds']
    if data.get('version') != VERSION or any(bounds[i] < covered[i] for i in (0, 1)) or any(bounds[i] > covered[i] for i in (2, 3)):
        raise ValueError('Hedge survey does not cover this scene')
    for row in data['records']:
        values = [row['widthM'], row['heightM'], row['uncertaintyM']]
        if row['worldTopM'] is not None:
            values.append(row['worldTopM'])
        if not np.isfinite(values).all() or min(values[:3]) <= 0:
            raise ValueError('Invalid measured hedge dimensions')
    return data['records'], [dict(id=path.name, sha256=hashlib.sha256(raw).hexdigest(), kind='hedge-survey', uncertaintyM=.15)]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bounds', type=float, nargs=4, required=True)
    parser.add_argument('--osm', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.output, args.bounds, args.osm), indent=2))
