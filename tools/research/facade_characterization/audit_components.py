"""Private read-only coverage audit: actual component meshes on each surveyed wall.

No area generation, screenshots, source mutations or photographic assets in git.
Coverage means at least one bounded component, never a claim of full wall fidelity.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.facade_components import components
from turku3d.facade_component_geometry import TRI, plan, geometry, surface_parts
from turku3d.package.gltf import GlbMesh, build_glb
from turku3d.wall_surfaces import validate_meshes
from turkutex.facade_sections import sections


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=C.data_root()/'facade-characterization/publications/ring42/programs.json')
    parser.add_argument('--baseline',type=Path,help='Frozen private inventory of the original 349 walls')
    parser.add_argument('--out',type=Path,default=C.work_root()/'reports/facade-components')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    programs=json.loads(args.source.read_text())
    baseline_path=args.baseline or args.out/'baseline.json'
    baseline=json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    if baseline and baseline['source_sha256'] != hashlib.sha256(args.source.read_bytes()).hexdigest():
        raise ValueError('baseline belongs to another publication')
    original=set(baseline['wall_ids']) if baseline else None
    rows=[]
    counts=Counter()
    kinds=Counter()
    failures=[]
    for program in programs:
        for wall in program['walls']:
            form=wall.get('structure',{})
            if not form.get('render_deferred'):
                continue
            paint,_=sections(form,program['storeys']['value'])
            was_unrendered=wall['wall_id'] in original if original is not None else not bool(paint)
            descriptors,pending=components(form,program['storeys']['value'])
            g=wall['geometry']
            coords=g.get('coordinates') or [g['p0'],g['p1']]
            a=np.array([coords[0][0]-C.ORIGIN_E,g.get('base_h',0),C.ORIGIN_N-coords[0][1]])
            b=np.array([coords[-1][0]-C.ORIGIN_E,a[1],C.ORIGIN_N-coords[-1][1]])
            vertices=np.array([a,b,b+[0,wall['height_m'],0],a+[0,wall['height_m'],0]])
            w={**wall,'geometry_components':descriptors}
            plans=plan(vertices,w)
            meshes,boxes=geometry(vertices,plans,program['building_id'],a)
            parts=list(surface_parts(vertices,vertices[:,[0,1]],plans))
            meshes += [GlbMesh('wall',p-a,TRI,uvs=uv) for p,uv in parts]
            try:
                validate_meshes(meshes,wall['wall_id'])
            except ValueError as error:
                failures.append(dict(wall=wall['wall_id'],error=str(error)))
            emitted=sorted(set(p['kind'] for p in plans))
            if was_unrendered:
                counts['previously_unrendered']+=1
                counts['newly_with_geometry' if emitted else 'still_without_geometry']+=1
                kinds.update(emitted)
            counts['deferred_with_geometry']+=bool(emitted)
            rows.append(dict(wall=wall['wall_id'],previously_unrendered=was_unrendered,
                             emitted=emitted,pending=pending,
                             slots=[d for d in descriptors if d['kind'] in {'artwork','passage'}],
                             component_count=len(plans),triangles=sum(len(m.indices) for m in meshes)))
            if emitted and not (args.out/'representative.glb').exists():
                (args.out/'representative.glb').write_bytes(build_glb(meshes,root_translation=a))
    if original is None:
        baseline_path.write_text(json.dumps(dict(source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),wall_ids=[w['wall'] for w in rows if w['previously_unrendered']]),indent=2)+'\n')
    result=dict(source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),counts=dict(counts),
                newly_supported_kinds=dict(kinds),invariant_failures=failures,walls=rows)
    (args.out/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='walls'},indent=2))
    if failures:
        raise SystemExit(1)


if __name__=='__main__':
    main()
