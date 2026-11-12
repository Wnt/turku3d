"""Bounded contact and dependency audit of a compiled real-source scene."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import shapely
from turku3d.col import read_col
from turku3d.ground.mesh import Mesh,HeightQuery
from turku3d.package.gltf import read_glb
from .schema import validate_scene


def audit(path,cache=None):
    path=Path(path);root=path.parent;s=validate_scene(json.loads(path.read_text()))
    def checked(url,sha):
        file=(root/url).resolve()
        if not file.is_relative_to(root.resolve()):raise ValueError('Scene dependency escapes release')
        data=file.read_bytes()
        if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Dependency hash mismatch: '+url)
        return data
    dependency_count=0
    for asset in s['assets']:
        raw=checked(asset['url'],asset['sha256']);doc,_=read_glb(raw)
        for image in doc.get('images',[]):
            if 'uri' in image:
                relative=str((Path(asset['url']).parent/image['uri']))
                checked(relative,Path(image['uri']).stem);dependency_count+=1
    for material in s['materials']:
        for key in ['baseColorTexture','normalTexture','ormTexture']:
            if key in material:
                texture=material[key];checked(texture['url'],texture['sha256']);dependency_count+=1
    positions=[];indices=[];materials=[];offset=0
    for identity,entry in s['collision'].items():
        raw=checked(entry['url'],entry['sha256']);col=read_col(raw)
        if col.id!=identity or len(col.indices)!=entry['tris']:raise ValueError('Collision identity mismatch')
        positions.append(col.positions+col.origin);indices.append(col.indices+offset);materials.append(col.materials);offset+=len(col.positions)
    collision=Mesh(np.concatenate(positions),np.concatenate(indices),np.concatenate(materials))
    faces=collision.positions[collision.indices]
    planar=np.abs(np.cross(faces[:,1]-faces[:,0],faces[:,2]-faces[:,0])[:,1])>1e-10
    query=HeightQuery(Mesh(collision.positions,collision.indices[planar],collision.material[planar]),tolerance=1e-6)
    meshes={m['id']:m for m in s['meshes']};samples=[];polygons=[]
    for support in s['supports']:
        m=meshes[support['meshId']];p=np.array(m['positions']).reshape(-1,3);ix=np.array(m['indices']).reshape(-1,3)
        t=p[ix];samples.extend(t.mean(axis=1)[::max(1,len(t)//20)])
        polygons.extend(shapely.polygons(t[:,:,[0,2]]))
    samples=np.asarray(samples);actual=query(samples[:,[0,2]],reference=samples[:,1]);errors=np.abs(actual-samples[:,1])
    if not np.isfinite(actual).all() or errors.max()>.002:raise ValueError('Visual/support/collision contact mismatch')
    trunk_count=0
    for fixture in s['fixtures']:
        if fixture['family']=='published-tree-trunk':
            trunk_count+=1
            mesh=meshes[fixture['meshIds'][0]]
            if mesh['collision']!='solid' or mesh.get('render',True):raise ValueError('Trunk collider policy mismatch')
            point=np.asarray(fixture['position']);hit=query([point[[0,2]]],reference=[point[1]])[0]
            if abs(hit-point[1])>.002:raise ValueError('Unsupported trunk base')
    report=dict(release=s['release'],supportSamples=len(samples),maxContactErrorM=float(errors.max()),
                assets=len(s['assets']),imageDependencies=dependency_count,fixtures=len(s['fixtures']),
                solidTreeTrunks=trunk_count,collisionTriangles=len(collision.indices))
    if cache:
        from .compile import load_evidence
        _,buildings,_,water,*_=load_evidence(Path(cache),s['bounds'])
        expected=shapely.box(*s['bounds']).difference(shapely.union_all(water)).difference(shapely.union_all([b.footprint for b in buildings]))
        actual=shapely.union_all(polygons);report['dryGroundM2']=expected.area;report['missingGroundM2']=expected.difference(actual).area
        if report['missingGroundM2']>.01:raise ValueError('Missing prepared support coverage')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('scene',type=Path);parser.add_argument('--cache',type=Path);args=parser.parse_args()
    print(json.dumps(audit(args.scene,args.cache),indent=2))
