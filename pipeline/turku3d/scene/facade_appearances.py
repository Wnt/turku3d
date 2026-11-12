"""Bounded reuse of the normal facade producer for qualified unseen buildings.

Trusted prepared WFS/ground pickles and a private metadata inventory enter here.
No image acquisition, vision call or bespoke wall editing occurs. All products
are external content-addressed GLBs/maps with source and generated-prior receipts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import pickle
import shutil
import time

import numpy as np

from turku3d import config as C
from .facade_transfer import qualified_buildings


def produce(wfs_path, ground_path, inventory_path, output, *, tile_id, library):
    from turku3d.coarse.extrusion import extrude
    from turku3d.coarse.stages import building_visual
    from turku3d.coarse.inputs import grid_axes,grid_heights
    from turku3d.coarse.visibility import bake,height_field
    from turku3d.coarse.archetypes import partition
    from turku3d.coarse.packaging import package_visual,shared_package_inputs
    from turku3d.layers import write_visual
    from turku3d.facade_programs import measured
    from turku3d.facade_kit import read_description
    from turku3d.package.gltf import read_glb

    if not shutil.which('toktx'):
        raise RuntimeError('The existing KTX tools must be on PATH before facade packaging')
    started=time.monotonic();output=Path(output);output.mkdir(parents=True,exist_ok=True)
    paths=[Path(wfs_path),Path(ground_path),Path(inventory_path)]
    raw=[p.read_bytes() for p in paths]
    wfs=pickle.loads(raw[0])['value'];ground=pickle.loads(raw[1])['value'];inventory=json.loads(raw[2])
    ids={str(r['buildingId']) for r in inventory['buildings']}
    all_buildings={b.key:b for b in [*wfs.get('wall_context',[]),*wfs['buildings']]}
    selected=[b for b in all_buildings.values() if str(b.facade_key) in ids]
    if {str(b.facade_key) for b in selected}!=ids:raise ValueError('Qualified appearances require every selected source building')
    buildings,receipts=qualified_buildings(selected,inventory)
    tile=C.VisualTile.parse(tile_id)
    shells=[(b,extrude(b)) for b in buildings]
    context=[(b,extrude(b)) for b in all_buildings.values() if str(b.facade_key) not in ids]
    meshes=building_visual(tile,shells,ortho=None,first_pass=False,wall_context=context)
    programs=[m.extras.pop('facade_program') for m in meshes if 'facade_program' in m.extras]
    (output/'generated-programs.json').write_text(json.dumps(programs,indent=2)+'\n')
    # The same source-only ambient method used by normal generation. No crown data
    # was admitted, so this bake claims building/ground occlusion only.
    terrain=grid_heights(ground['points'],*grid_axes(tile))
    meshes=bake(meshes,height_field(tile,[*buildings,*[b for b,_ in context]],terrain))
    if any(not np.isfinite(m.positions).all() or
           (m.uvs is not None and not np.isfinite(m.uvs).all()) for m in meshes):
        raise ValueError('Facade producer returned nonfinite geometry or UVs')
    _,models=partition(tile,meshes,shells)
    if {key for key,_,_,_ in models}!=ids:raise ValueError('Facade producer omitted a selected building')
    shared=shared_package_inputs(Path(library),output)
    assets=[];counts=[]
    for key,origin,parts,_ in models:
        stage=output/'buildings'/key;stage.mkdir(parents=True,exist_ok=True)
        source=stage/'building.vis.glb';write_visual(source,parts,root_translation=(0.,0.,0.))
        payload,stats=package_visual(C.VisualTile(0,0,0),[source],stage,output,Path(library),
                                    cache_unit='qualified-facade/'+key,shared=shared)
        sha=hashlib.sha256(payload).hexdigest();(output/'shared'/(sha+'.glb')).write_bytes(payload)
        low=(np.asarray(stats['bounds'][0])+origin).tolist();high=(np.asarray(stats['bounds'][1])+origin).tolist()
        assets.append(dict(id=key,entityId=key,url='shared/'+sha+'.glb',sha256=sha,
                           position=origin.tolist(),recipe='building',bounds={'min':low,'max':high}))
        document,_=read_glb(payload)
        for image in document.get('images',[]):
            if 'uri' in image:
                path=(output/'shared'/image['uri']).resolve()
                if not path.is_relative_to((output/'shared').resolve()) or not path.exists():raise ValueError('Missing or escaped generated image dependency')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=path.stem:raise ValueError('Generated image hash mismatch')
        counts.append({'buildingId':key,'triangles':stats['tris'],'draws':stats['draws'],'bytes':len(payload),
                       'retainedReviewedProgram':bool(measured(key)),'retainedDescription':read_description(key) is not None})
        print(key,stats['tris'],'triangles',len(payload),'bytes',flush=True)
    source_pins=[dict(name=p.name,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)) for p,data in zip(paths,raw)]
    provenance={'inputs':source_pins,'transfer':receipts,'counts':counts,'seconds':time.monotonic()-started,
                'geometry':'Unchanged source base/top/footprint/roof planes; normal extrusion and exposed-wall assembly.',
                'appearance':'Existing facade grammar and texture kit. Sampled opening/ornament/paint/unknown material remain priors.',
                'roof':'Existing material fallback with no photograph input.',
                'ambient':'Existing building/ground height-field visibility; no canopy input.',
                'noNewVision':True}
    (output/'assets.json').write_text(json.dumps(assets,indent=2)+'\n')
    (output/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    return assets,provenance


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('wfs','ground','inventory','output','library'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--tile',required=True);args=parser.parse_args()
    assets,receipt=produce(args.wfs,args.ground,args.inventory,args.output,tile_id=args.tile,library=args.library)
    print(json.dumps({'assets':len(assets),'seconds':receipt['seconds'],'output':str(args.output)}))
