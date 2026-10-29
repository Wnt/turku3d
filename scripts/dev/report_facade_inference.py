"""Compare generated matched-wall fields against an earlier resolver, without a tile build."""
import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from turku3d import config as C
from turku3d.facade_programs import measured, resolve, compile_wall
from turku3d.facade_program_geometry import wall_parts


def fixture(key):
    observed = measured(key)
    wall = observed['walls'][-1]
    g = wall['geometry']
    a, b = np.asarray(g['p0']), np.asarray(g['p1'])
    base, height = g['base_h'], wall['height_m']
    vertices = np.array([[a[0]-C.ORIGIN_E,base,C.ORIGIN_N-a[1]],
                         [b[0]-C.ORIGIN_E,base,C.ORIGIN_N-b[1]],
                         [b[0]-C.ORIGIN_E,base+height,C.ORIGIN_N-b[1]],
                         [a[0]-C.ORIGIN_E,base+height,C.ORIGIN_N-a[1]]])
    normal = np.asarray(g['normal'])
    normal = normal * [1, -1] if len(normal) == 2 else normal[[0, 2]]
    if np.dot(normal, (vertices[1]-vertices[0])[[2, 0]] * [1, -1]) < 0:
        vertices = vertices[[1, 0, 3, 2]]
    attrs = {**observed['attributes'], 'construction_year': observed['attributes']['year']}
    building = SimpleNamespace(key=key, facade_key=key, base=base, top=base+height,
        footprint=shapely.LineString(vertices[:2, [0, 2]]).buffer(5), attributes=attrs)
    return building, vertices, {0:base+height}


def report(baseline):
    spec = importlib.util.spec_from_file_location('turku3d.before', baseline)
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    result = {}
    for key in ('103295896N', '103295899S'):
        building, vertices, eaves = fixture(key)
        result[key] = {}
        for label, fn in [('before', before.resolve), ('after', resolve)]:
            program = fn(building, vertices, eaves)
            wall = program['walls'][0]
            assert wall.get('measured_wall_id'), (key, 'unmatched fixture')
            style = compile_wall(wall, program['storeys']['value'])
            parts = list(wall_parts(vertices, wall, style))
            result[key][label] = dict(wall_id=wall['measured_wall_id'], archetype=program['archetype'],
                style=style, wall=wall['wall'], trim=wall['trim'], bay_count=wall['bays']['count'],
                openings=len(wall['windows']['rectangles_m']), paint_parts=len(parts),
                details=wall.get('appearance_details'))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(report(args.baseline), indent=2))
