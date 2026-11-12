"""Compile mapped, surveyed bridge structure into explicit prepared products."""
from __future__ import annotations
import hashlib
import pickle
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import shapely
from turku3d import config as C


def source_records(cache, bounds):
    records=[]
    x,z,X,Z=bounds
    for i in range(int(x//250),int(np.ceil(X/250))):
        for j in range(int(z//250),int(np.ceil(Z/250))):
            root=Path(cache)/f't0_{i}_{j}'
            for name in ('roads','stairs'):
                files=sorted(root.glob(name+'.*.pkl'))
                if len(files)>1:raise ValueError('Ambiguous structure evidence: '+str(root/name))
                if files:
                    raw=files[0].read_bytes()
                    records.append((dict(id=f'{root.name}/{files[0].name}',sha256=hashlib.sha256(raw).hexdigest(),kind=name,uncertaintyM=.15),pickle.loads(raw)['value']))
    return records


def prepare(cache,bounds,points,buildings,water,walks):
    from turku3d.coarse.bridges import produce
    records=source_records(cache,bounds);ways={};stairs={}
    window=shapely.box(*bounds)
    for source,value in records:
        if source['kind']=='roads':
            for way in value.get('bridge_ways',[]):
                if way.get('outline',way['line']).intersects(window):ways[str(way['id'])]=way
        else:
            for row in value[0]:stairs[hashlib.sha256(pickle.dumps(row)).hexdigest()]=row
    if not ways:return [],[s for s,_ in records]
    inputs=SimpleNamespace(points=points,buildings=buildings,water=water,walkable=walks,
                           roads={'bridge_ways':list(ways.values())},stairs=(list(stairs.values()),()))
    # The adapter compiles the whole bounded scene, never clips a deck at its old
    # source tile edge. All measured upper/lower levels remain distinct products.
    tile=SimpleNamespace(size=250.,bbox=C.BBoxLocal(*bounds))
    decks=produce(tile,points.copy(),inputs)
    return decks,[s for s,_ in records]


def approach_boundaries(decks):
    result=[]
    for deck in decks:
        for patch in deck.aprons:
            result.extend([patch['area'].boundary,patch['line'].buffer(patch['width']/2,cap_style='flat').difference(patch['blocked']).boundary])
    return result


def approach_owners(decks,polygon):
    point=polygon.representative_point()
    return [p for d in decks for p in d.aprons if p['area'].covers(point)]


def grade_approach(xz,y,patches):
    from turku3d.coarse.bridge_join import grade
    if not patches:return y
    prepared=[]
    for patch in patches:
        if '_edge_prepared' not in patch:
            patch['_edge_prepared']={**patch,'blocked':patch['blocked'].buffer(-1e-6)}
        prepared.append(patch['_edge_prepared'])
    return grade(np.c_[xz[:,0],y,xz[:,1]],prepared,edges=False)[:,1]


def append(scene,decks):
    from .compile import mesh_record
    from turku3d.coarse.bridge_materials import maps,METRES
    materials={m['id'] for m in scene['materials']}
    for deck in decks:
        for mesh in deck.meshes:
            surface=mesh.metadata.get('surface','concrete')
            if surface=='night_rail':surface='paint'
            material='bridge:'+mesh.material+':'+surface
            if material not in materials:
                descriptor=dict(id=material,color=[1.,1.,1.],roughness=1.,metalness=1.)
                for field,key in (('baseColorTexture','image'),('normalTexture','normal_image'),('ormTexture','orm_image')):
                    sha=hashlib.sha256(maps(surface)[key]).hexdigest()
                    descriptor[field]=dict(url='shared/'+sha+'.png',sha256=sha,wrap='repeat')
                scene['materials'].append(descriptor);materials.add(material)
            # Producer material identifies the traversable top explicitly. Body,
            # rail, pier and decoration meshes never become support surfaces.
            supported=mesh.material in ('road','sidewalk','gravel')
            sid=mesh.object_id+':support' if supported else None
            record=mesh_record(mesh.object_id,'bridge:'+str(deck.way['id']),material,mesh.positions,mesh.indices,
                               'solid' if mesh.metadata.get('collision',True) else 'passable',sid,mesh.uvs/METRES)
            record['render']=mesh.visual
            scene['meshes'].append(record)
            if supported:
                # Only exterior top edges define the canonical bridge boundary;
                # source station cuts stay inside this one support surface.
                p=np.asarray(record['positions']).reshape(-1,3);ix=np.asarray(record['indices']).reshape(-1,3)
                rounded=np.round(p,7);vertices,inverse=np.unique(rounded,axis=0,return_inverse=True)
                edges=np.sort(np.concatenate([inverse[ix[:,s]] for s in ([0,1],[1,2],[2,0])]),axis=1)
                edges,count=np.unique(edges,axis=0,return_counts=True);bids=[]
                known={b['id'] for b in scene['boundaries']}
                for edge in edges[count==1]:
                    pos=vertices[edge];bid='edge:'+hashlib.sha256(pos.tobytes()).hexdigest()[:16]
                    if bid not in known:scene['boundaries'].append(dict(id=bid,positions=pos.tolist()));known.add(bid)
                    bids.append(bid)
                scene['supports'].append(dict(id=sid,kind='road' if mesh.material=='road' else 'footway',boundaryIds=bids,meshId=record['id']))


def write_textures(scene,output):
    from turku3d.coarse.bridge_materials import maps
    for material in scene['materials']:
        if not material['id'].startswith('bridge:'):continue
        surface=material['id'].split(':')[-1]
        for field,key in (('baseColorTexture','image'),('normalTexture','normal_image'),('ormTexture','orm_image')):
            raw=maps(surface)[key];texture=material[field]
            if hashlib.sha256(raw).hexdigest()!=texture['sha256']:raise ValueError('Bridge texture recipe changed')
            path=Path(output)/texture['url'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
