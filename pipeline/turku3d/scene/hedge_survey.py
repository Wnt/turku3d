"""Freeze bounded, measured hedge crowns and mapped path openings as local inputs."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import shapely

VERSION = 'turku-hedge-survey/1'


def coherent_crown(line, points, classes):
    """Separate a continuous clipped crown from sparse overhanging tree returns.

    A candidate layer must occupy at least 80% of 2.5m stations, each with six
    vegetation returns. The lowest continuous layer above local ground owns a
    mapped hedge; disconnected higher branches cannot lift it. A relative-height
    profile handles slopes when no world-height layer has that coverage.
    """
    from scipy.spatial import cKDTree
    from turku3d.coarse.hedges import dimensions
    ground = points[classes == 2]
    vegetation = points[classes != 2]
    if len(ground) < 10 or len(vegetation) < 30 or line.length < 2:
        return None, None
    distance, owner = cKDTree(ground[:, [0, 2]]).query(vegetation[:, [0, 2]])
    heights = vegetation[:, 1]-ground[owner, 1]
    offset = shapely.distance(line, shapely.points(vegetation[:, [0, 2]]))
    eligible = (distance < 4) & (offset < .9) & (heights > .45) & (heights < 4.5)
    if eligible.sum() < 30:
        return None, None
    along = shapely.line_locate_point(line, shapely.points(vegetation[eligible][:, [0, 2]]))
    bins = max(1, int(np.ceil(line.length/2.5)))
    station = np.minimum((along/line.length*bins).astype(int), bins-1)
    for mode, values in [('world-height', vegetation[:, 1]), ('above-ground', heights)]:
        candidate = values[eligible];qualified = []
        for centre in np.arange(np.floor(candidate.min()/.05)*.05, candidate.max()+.05, .05):
            counts = np.bincount(station[abs(candidate-centre) < .25], minlength=bins)
            coverage = int((counts >= 6).sum())
            if coverage >= np.ceil(bins*.8):
                qualified.append((float(centre), coverage, int(np.minimum(counts, 50).sum())))
        if not qualified:
            continue
        # Adjacent successful bands describe one crown. Prefer the strongest
        # band in its lowest connected population, not its lower edge.
        first = [qualified[0]]
        for row in qualified[1:]:
            if row[0]-first[-1][0] > .075:
                break
            first.append(row)
        centre, coverage, density = max(first, key=lambda row: (row[1], row[2], -row[0]))
        selected = abs(values-centre) < .4
        filtered = np.r_[ground, vegetation[selected]]
        labels = np.r_[np.full(len(ground), 2), classes[classes != 2][selected]]
        measured = dimensions(line, filtered, labels)
        if measured is not None:
            return measured, dict(mode=mode, centreM=round(centre, 3), coveredStations=coverage,
                                  stations=bins, stationLengthM=2.5, densityScore=density)
    return None, None


def freeze(output, bounds, osm):
    from turku3d import config as C
    from turku3d.spatial import rows
    from turku3d.ground.inputs import laz_paths, read_returns
    from turku3d.coarse.hedges import crossing_mask

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
        measured, profile = coherent_crown(line, points[selected], classes[selected])
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
                            uncertaintyM=.15, returns=int(selected.sum()), profile=profile))
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
