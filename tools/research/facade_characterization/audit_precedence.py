"""Repaint a frozen building/source snapshot and report every wall's paint authority.

The snapshot is private derived evidence: key, facade_key, base/top, material_key,
footprint WKT, roof_parts [(WKT, plane)], attributes and sanitized facade assets.
No acquisition, input mutation, tile build or image render is performed.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
import shapely
from turku3d.coarse import stored_facades
from turku3d.coarse.extrusion import extrude
from turku3d.coarse.inputs import Building
from turku3d.wall_surfaces import validate_meshes


def audit(source):
    data = json.loads(source.read_text())
    b = Building(data['key'], shapely.from_wkt(data['footprint']), data['base'], data['top'],
                 data['material_key'])
    b.facade_key, b.attributes = data['facade_key'], data['attributes']
    b.roof_parts = [(shapely.from_wkt(wkt), np.array(plane)) for wkt, plane in data['roof_parts']]
    walls = stored_facades.assemble_walls([(b, extrude(b))])[0]
    with patch.object(stored_facades, 'building', return_value=data['assets']):
        meshes = stored_facades.wall_meshes(b, walls, np.zeros(3))
    program = next(m.extras['facade_program'] for m in meshes if 'facade_program' in m.extras)
    counts = Counter(w['paint_source'] for w in program['walls'])
    report = dict(building=b.facade_key, sources=counts, storeys=program['storeys'],
                  archetype=program['archetype'], description_source=program.get('description_source'),
                  validation=validate_meshes(meshes, 'precedence-audit'), walls=[])
    for points, wall in zip(walls.reshape(-1, 4, 3), program['walls'], strict=True):
        report['walls'].append(dict(
            wall_id=wall['wall_id'], coordinates=points.tolist(), width_m=wall['width_m'],
            height_m=wall['height_m'], source=wall['paint_source'], colour=wall['wall']['colour'],
            material=wall['wall']['material'], stored_map=wall.get('stored_facade_index'),
            reviewed_wall=wall.get('measured_wall_id'), sections=wall.get('render_sections'),
            components=wall.get('rendered_components'),
        ))
    styles = [s for m in meshes for s in m.extras.get('facade_kit_walls', [])]
    report['opening_patches'] = sum(s['detail'][3] > 0 and s['detail'][3] % 4 == 1 for s in styles)
    report['stored_meshes'] = sum(m.extras.get('material_key') == 'facade_stored' for m in meshes)
    assert not report['validation']['violations'], report['validation']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.snapshot)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'walls'}))


if __name__ == '__main__':
    main()
