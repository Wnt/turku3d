"""Attach the registered Qwensel frontage and supported entrance as one product."""
import numpy as np
from .compile import mesh_record
from .fixtures import support_query
from .structures import add_support
from .qwensel_details import (QWENSEL_FRONTAGE,QWENSEL_MATERIALS,frontage_anchor,
                              qwensel_window_details,qwensel_entrance_details,qwensel_entrance_supports)


def append_qwensel(scene):
    identity=QWENSEL_FRONTAGE['buildingId'];asset=next((a for a in scene['assets'] if a['entityId']==identity),None)
    authored=next((s for s in scene['sources'] if s['id']==f'building:{identity}:authored' and s['sha256']==QWENSEL_FRONTAGE['authoredGlbSha256'] and s['kind']=='approved-building'),None)
    frontage=next((s for s in scene['sources'] if asset and s['id']==f'building:{identity}:frontage' and s['sha256']==asset['sha256'] and s['kind']=='registered-frontage-appearance'),None)
    if not (asset and authored and frontage):return
    scene['materials'].extend(QWENSEL_MATERIALS)
    query=support_query(scene)
    def ground(x,z):
        hit=query(x,z,reference_y=QWENSEL_FRONTAGE['baseHeight'])
        if hit is None:raise ValueError(f'Qwensel entrance lacks canonical ground support at {x},{z}')
        return hit[1]
    entrance_ids=[]
    for window,pieces in ((True,qwensel_window_details()),(False,qwensel_entrance_details(support_height=ground))):
        for piece in pieces:
            record=mesh_record(piece['id'],identity,piece['materialId'],np.asarray(piece['positions']).reshape(-1,3),
                               np.asarray(piece['indices']).reshape(-1,3),piece['collision'])
            scene['meshes'].append(record)
            if not window:entrance_ids.append(record['id'])
    for piece in qwensel_entrance_supports(support_height=ground):
        key='qwensel:'+piece['id']
        record=mesh_record(key,identity,piece['materialId'],np.asarray(piece['positions']).reshape(-1,3),
                           np.asarray(piece['indices']).reshape(-1,3),'passable',key+':support')
        record['render']=False;scene['meshes'].append(record);add_support(scene,record,'footway')
    entry=QWENSEL_FRONTAGE['entrance'];position,yaw=frontage_anchor(QWENSEL_FRONTAGE['door']['along'],entry['landingHeight'])
    middle=entry['backOffset']+entry['depth']/2;c,s=np.cos(yaw),np.sin(yaw)
    x,z=position[0]+s*middle,position[2]+c*middle;support,base=query(x,z,reference_y=QWENSEL_FRONTAGE['baseHeight'])
    records=[m for m in scene['meshes'] if m['id'] in entrance_ids];top=max(max(m['positions'][1::3]) for m in records)
    scene['fixtures'].append(dict(id=identity+':raised-entrance',family='qwensel-opposed-timber-entrance-v1',
        supportId=support['id'],position=[x,base,z],yaw=float(yaw),dimensions=[entry['landingWidth']+2*entry['flightRun'],top-base,entry['depth']],
        meshIds=entrance_ids,emitterIds=[],sourceIds=[authored['id'],frontage['id'],*[s['id'] for s in scene['sources'] if s['kind']=='raw-ground-survey']],uncertaintyM=.05))
