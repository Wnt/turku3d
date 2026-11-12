"""Restore observed under-crest bars and masonry wash on explicit shoreline faces.

The original solids remain collision authority. Coplanar visible subdivisions
preserve their metric UVs; only the qualified wall receives the bounded wash.
Existing coarse source rules own eligibility, bar dimensions, tint and falloff.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from turku3d.col.materials import mat_id
from turku3d.coarse import quay_lighting as observed
from .compile import mesh_record
from .fixtures import support_query

RULES = Path(__file__).parents[1]/'night_lighting.json'


def append(scene, shoreline, walks, corridors):
    if not corridors or not shoreline:return {}
    rules=json.loads(RULES.read_text())
    policy=json.loads(observed.OBSERVATIONS.read_text())
    tint=np.asarray(policy['linear_rgb'])
    source=dict(id=scene['id']+':quay-lighting',kind='compiled-quay-lighting',uncertaintyM=.3,
        sha256=hashlib.sha256(Path(__file__).read_bytes()+Path(observed.__file__).read_bytes()+RULES.read_bytes()).hexdigest())
    support_at=support_query(scene)
    eligible=observed.eligibility(walks,corridors)
    tile=SimpleNamespace(id=scene['id'],origin=np.zeros(3))
    washes={};new=[];fixtures=[];bar_material=None
    for original in shoreline:
        p=np.asarray(original['positions']).reshape(-1,3)
        ix=np.asarray(original['indices']).reshape(-1,3)
        uv=np.asarray(original['uv']).reshape(-1,2)
        keep=[];qualified=False
        for i in range(0,len(ix),2):
            pair=ix[i:i+2]
            if len(pair)!=2:keep.extend(pair);continue
            points,inverse=np.unique(p[pair].reshape(-1,3),axis=0,return_inverse=True)
            bank=SimpleNamespace(positions=points,indices=inverse.reshape(-1,3),material=np.full(2,mat_id('quay')))
            products=observed.visuals(tile,bank,walks,corridors,.05,eligible=eligible)
            wall=next((g for g in products if g.name=='quay-lit-stone'),None)
            bar=next((g for g in products if g.name=='quay-light-bars'),None)
            if wall is None or bar is None:keep.extend(pair);continue
            top=points[np.argsort(points[:,1])[-2:]];centre=top.mean(axis=0)
            normal=np.cross(p[pair[0,1]]-p[pair[0,0]],p[pair[0,2]]-p[pair[0,0]])
            normal/=np.linalg.norm(normal)
            # The fixture is fastened below the mapped crest; its support anchor
            # lies immediately inland on that crest's real prepared surface.
            anchor=centre-normal*.05
            hit=support_at(anchor[0],anchor[2],reference_y=centre[1])
            if hit is None or abs(hit[1]-centre[1])>.15:keep.extend(pair);continue
            bar_material=dict(id='quay-light-bar',color=list(bar.color[:3]),roughness=bar.roughness,metalness=0,
                nightEmission=(tint*rules['materials']['night_quay_bar']['intensity']).tolist())
            support,y=hit;anchor[1]=y
            key=f"{original['entityId']}:light:{i//2}"
            # An affine map on the original planar face exactly preserves its
            # source UVs through the new vertical lighting subdivisions.
            transform=np.linalg.lstsq(np.c_[p[pair].reshape(-1,3),np.ones(6)],uv[pair].reshape(-1,2),rcond=None)[0]
            positions=wall.positions
            wall_uv=np.c_[positions,np.ones(len(positions))]@transform
            visible=mesh_record(key+':wash',original['entityId'],original['materialId'],positions,wall.indices,
                'passable',uv=wall_uv)
            if original.get('boundaryIds'):visible['boundaryIds']=original['boundaryIds']
            values=np.asarray(wall.night_irradiance)[wall.indices].reshape(-1,3)*rules['quay']['washIntensity']
            if values.size!=len(visible['positions']):raise ValueError('Quay wash has degenerate source triangles')
            washes[visible['id']]=values.ravel().tolist();new.append(visible)
            light=mesh_record(key+':bar',key,'quay-light-bar',bar.positions,bar.indices,'passable',support['id'])
            new.append(light)
            direction=top[1]-top[0]
            fixtures.append(dict(id=key,family='observed-under-crest-bar',supportId=support['id'],position=anchor.tolist(),
                yaw=float(np.arctan2(-direction[2],direction[0])),dimensions=[float(np.linalg.norm(direction)),.08,.025],
                meshIds=[light['id']],emitterIds=[],sourceIds=['quay-lighting.json',source['id']],uncertaintyM=.3))
            qualified=True
        if qualified:
            original['render']=False
            if keep:
                new.append(mesh_record(original['id']+':unlit',original['entityId'],original['materialId'],p,keep,'passable',uv=uv))
    if not fixtures:return {}
    scene['sources'].append(source)
    scene['materials'].append(bar_material)
    scene['meshes'].extend(new);scene['fixtures'].extend(fixtures)
    return washes


def apply_wash(scene, washes):
    """Add the observed wall-only term after the unchanged direct street bake."""
    for mesh in scene['meshes']:
        if mesh['id'] in washes:
            base=np.asarray(mesh.get('nightIrradiance',np.zeros(len(mesh['positions']))))
            mesh['nightIrradiance']=(base+np.asarray(washes[mesh['id']])).tolist()
