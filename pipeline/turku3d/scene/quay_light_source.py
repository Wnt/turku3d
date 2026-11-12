"""Freeze the existing observed quay-light reach and mapped pedestrian eligibility.

No imagery ships. The source file pins the OSM input and observed policy; its
polygons are eligibility corridors, not inferred locations of surveyed fixtures.
"""
import argparse
import hashlib
import json
from pathlib import Path

import shapely
from turku3d import config as C
from turku3d.coarse import quay_lighting as observed

SCHEMA = 'turku-quay-lighting-source/1'
NAME = 'quay-lighting.json'


def prepare(osm, bounds, output):
    paths = observed.source_paths(Path(osm))
    x,z,X,Z = bounds
    corridors = []
    import math
    for i in range(math.floor(x/250), math.ceil(X/250)):
        for j in range(math.floor(z/250), math.ceil(Z/250)):
            corridors.extend(observed.prepare(C.VisualTile(0,i,j), paths))
    area = shapely.union_all(corridors).intersection(shapely.box(*bounds).buffer(12))
    record = dict(schema=SCHEMA, bounds=list(bounds),
        corridors=[json.loads(shapely.to_geojson(p)) for p in shapely.get_parts(area)],
        sources=[dict(id=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in dict.fromkeys(paths)],
        policy=json.loads(observed.OBSERVATIONS.read_text()))
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(record,sort_keys=True,separators=(',',':'))+'\n')
    return record


def load(cache, bounds):
    path=Path(cache)/NAME
    if not path.exists():return [],[]
    raw=path.read_bytes();record=json.loads(raw)
    if set(record)!={'schema','bounds','corridors','sources','policy'} or record['schema']!=SCHEMA:
        raise ValueError('Invalid quay-light source')
    if record['bounds']!=list(bounds):raise ValueError('Quay-light source bounds mismatch')
    if record['policy']!=json.loads(observed.OBSERVATIONS.read_text()):
        raise ValueError('Quay-light source policy changed; prepare a new source pin')
    for source in record['sources']:
        if set(source)!={'id','sha256'} or not source['id'] or len(source['sha256'])!=64:
            raise ValueError('Invalid quay-light provenance')
    corridors=[shapely.from_geojson(json.dumps(p)) for p in record['corridors']]
    if any(p.geom_type not in ('Polygon','MultiPolygon') or not p.is_valid or p.is_empty for p in corridors):
        raise ValueError('Invalid quay-light corridor')
    return corridors,[dict(id=NAME,sha256=hashlib.sha256(raw).hexdigest(),kind='quay-lighting-corridors',uncertaintyM=.3)]


def extend_recipe(recipe_path, cache, output):
    """Pin the qualified input onto a current recipe, preserving every other field."""
    from .recipe import source_pins,validate_recipe
    recipe=validate_recipe(json.loads(Path(recipe_path).read_text()))
    if not load(cache,recipe['bounds'])[1]:raise ValueError('Copy the frozen quay-light source into the current cache first')
    recipe['inputs']=source_pins(cache,recipe['bounds'])
    validate_recipe(recipe)
    Path(output).write_text(json.dumps(recipe,indent=2)+'\n')
    return recipe


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--osm',type=Path,required=True);p.add_argument('--bounds',nargs=4,type=float,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=prepare(a.osm,a.bounds,a.output)
    print(json.dumps(dict(path=str(a.output),corridors=len(result['corridors']))))
