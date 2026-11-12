"""Bounded import of published distant scenery; cut overlap in the producer."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request
import numpy as np
import shapely
from turku3d.package.gltf import GlbMesh,build_glb


def clip_polygon(vertices,axis,edge,sign):
    out=[]
    for a,b in zip(vertices,np.roll(vertices,-1,axis=0),strict=True):
        ia,ib=sign*(a[axis]-edge)>=0,sign*(b[axis]-edge)>=0
        if ia:out.append(a)
        if ia!=ib:
            lo,hi=(a,b) if a[axis]<b[axis] else (b,a)
            p=lo+(hi-lo)*((edge-lo[axis])/(hi[axis]-lo[axis]));p[axis]=edge;out.append(p)
    return np.array(out)


def outside_mesh(row,bounds,occupied_buildings=()):
    p=np.asarray(row['positions']);ix=np.asarray(row['indices']).reshape(-1,3);color=np.asarray(row['colors'])
    if occupied_buildings and row['layer'] in ('buildings','buildings-lod-glazed'):
        ownership=shapely.union_all(occupied_buildings).buffer(.3)
        centres=p[ix].mean(axis=1)
        ix=ix[~shapely.intersects_xy(ownership,centres[:,0],centres[:,2])]
    if not len(ix):return None
    data=np.c_[p,color];out=[]
    xmin,zmin,xmax,zmax=bounds
    planes=[[(0,xmin,-1)],[(0,xmax,1)],[(0,xmin,1),(0,xmax,-1),(2,zmin,-1)],[(0,xmin,1),(0,xmax,-1),(2,zmax,1)]]
    tris=p[ix];low=tris.min(axis=1);high=tris.max(axis=1)
    exterior=(high[:,0]<=xmin)|(low[:,0]>=xmax)|(high[:,2]<=zmin)|(low[:,2]>=zmax)
    out.extend(data[ix[exterior]])
    touching=~exterior&~((low[:,0]>=xmin)&(high[:,0]<=xmax)&(low[:,2]>=zmin)&(high[:,2]<=zmax))
    for triangle in data[ix[touching]]:
        for cuts in planes:
            polygon=triangle
            for axis,edge,sign in cuts:
                if len(polygon)<3:break
                polygon=clip_polygon(polygon,axis,edge,sign)
            for k in range(1,len(polygon)-1):out.append(polygon[[0,k,k+1]])
    if not out:return None
    vertices=np.asarray(out).reshape(-1,6);faces=np.arange(len(vertices)).reshape(-1,3)
    t=vertices[:,:3][faces];keep=np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1)>2e-6
    return GlbMesh(row['name'],vertices[:,:3],faces[keep],colors=vertices[:,3:],color=(1,1,1,1),extras=dict(layer=row['layer'],scenery=True))


def import_context(catalogue,output,scratch,bounds,*,occupied_tree_ids,water,occupied_buildings=()):
    output,scratch=Path(output),Path(scratch);scratch.mkdir(parents=True,exist_ok=True);shared=output/'shared';shared.mkdir(parents=True,exist_ok=True)
    assets=[];tree_records=[];tree_sources=[]
    for source in catalogue['tiles']:
        if source['tile'].startswith('t2_'):continue
        tree_path=scratch/(source['tile']+'.trees.json')
        if not tree_path.exists():
            raw=urllib.request.urlopen('https://city.lab.madekivi.fi/coarse/'+source['trees_url'],timeout=30).read()
            tree_data=json.loads(raw)
            if tree_data.get('version')!=1 or 'nodes' not in tree_data:
                raise ValueError('Context tree source is still preparing; retry after its bounded source job')
            tree_path.write_bytes(raw)
        raw=tree_path.read_bytes();tree_records.append(json.loads(raw))
        tree_sources.append(dict(id=source['trees_url'],sha256=hashlib.sha256(raw).hexdigest(),kind='mapped-context-trees',uncertaintyM=.3))
        url='https://city.lab.madekivi.fi/coarse/scenery/'+source['key']+'.json'
        manifest=json.loads(urllib.request.urlopen(url,timeout=30).read());record=manifest['visual'][source['tile']]
        url=urllib.parse.urljoin('https://city.lab.madekivi.fi/coarse/'+source['tile']+'/',record['url'])
        raw=urllib.request.urlopen(url,timeout=30).read()
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('Context source hash mismatch')
        path=scratch/(source['tile']+'.glb');path.write_bytes(raw);decoded=path.with_suffix('.json')
        subprocess.run(['node',str(Path(__file__).with_name('decode_context.mjs')),str(path),str(decoded)],check=True)
        meshes=[]
        for row in json.loads(decoded.read_text()):
            if row['layer'] in ('night-architecture','vegetation'):continue
            mesh=outside_mesh(row,bounds,occupied_buildings)
            if mesh is not None:meshes.append(mesh)
        data=build_glb(meshes);sha=hashlib.sha256(data).hexdigest();(shared/(sha+'.glb')).write_bytes(data)
        positions=np.concatenate([m.positions for m in meshes]);assets.append(dict(id='context:'+source['tile'],entityId='context:'+source['tile'],url='shared/'+sha+'.glb',sha256=sha,position=[0,0,0],recipe='context',bounds=dict(min=positions.min(axis=0).tolist(),max=positions.max(axis=0).tolist())))
    from .context_trees import compile_context_trees
    tree_asset,identities=compile_context_trees(tree_records,output,occupied_ids=occupied_tree_ids,bounds=bounds,water=water)
    if tree_asset:assets.append(tree_asset)
    (scratch/'tree-import.json').write_text(json.dumps(dict(sources=tree_sources,ids=identities),indent=2)+'\n')
    return assets
