"""Resolve mapped furniture against final semantic supports before emitting it."""
import numpy as np
import shapely
from .families import promenade_bench, litter_bin, PROMENADE_BENCH, LITTER_BIN
from .compile import mesh_record


def place_furniture(scene, props):
    surfaces = []
    by_id = {m['id']:m for m in scene['meshes']}
    for support in scene['supports']:
        mesh = by_id[support['meshId']]
        p = np.asarray(mesh['positions']).reshape(-1,3)
        ix = np.asarray(mesh['indices']).reshape(-1,3)
        xy = p[ix][:,:,[0,2]]
        polygons = shapely.polygons(xy)
        surfaces.append((support, p[ix], polygons))
    source_ids = [s['id'] for s in scene['sources'] if s['kind']=='poles']
    def support_at(x,z):
        for support,tris,polygons in surfaces:
            selected=np.flatnonzero(shapely.intersects_xy(polygons,x,z))
            if len(selected):
                a,b,c=tris[selected[0]]
                normal=np.cross(b-a,c-a)
                if abs(normal[1])<1e-8:
                    continue
                y=a[1]-(normal[0]*(x-a[0])+normal[2]*(z-a[2]))/normal[1]
                return support,float(y)
        return None
    for prop in props:
        if prop.kind not in ('bench','bin'):
            continue
        hit=support_at(prop.x,prop.z)
        if hit is None:
            continue
        support,y=hit
        # Family choice stays conservative: observed backless river bench only.
        backless=prop.object_id.endswith('7df902e13c6c4e95ec992bf1f144b1773b4710d2')
        yaw=.45 if prop.kind=='bench' else prop.yaw
        family=PROMENADE_BENCH if prop.kind=='bench' else LITTER_BIN
        dimensions=list(family['dimensions'])
        if backless:
            dimensions[1]=.45
        c,s=np.cos(yaw),np.sin(yaw)
        checks=[]
        for x,z in [(-dimensions[0]/2,-dimensions[2]/2),(dimensions[0]/2,-dimensions[2]/2),
                    (dimensions[0]/2,dimensions[2]/2),(-dimensions[0]/2,dimensions[2]/2)]:
            checks.append(support_at(prop.x+c*x+s*z,prop.z-s*x+c*z))
        if any(p is None for p in checks) or max(abs(p[1]-y) for p in checks)>.08:
            continue
        position=[prop.x,y,prop.z]
        pieces=promenade_bench(position,yaw,backrest=not backless) if prop.kind=='bench' else litter_bin(position,yaw)
        ids=[]
        for piece in pieces:
            key=f"{prop.object_id}:{piece['id']}"
            record=mesh_record(key,prop.object_id,piece['materialId'],np.array(piece['positions']).reshape(-1,3),
                               np.array(piece['indices']).reshape(-1,3),piece['collision'],support['id'])
            scene['meshes'].append(record);ids.append(key)
        scene['fixtures'].append(dict(id=prop.object_id,family=family['family']+(':backless' if backless else ''),
                                      supportId=support['id'],position=position,yaw=yaw,dimensions=dimensions,
                                      meshIds=ids,emitterIds=[],sourceIds=source_ids,uncertaintyM=.3))
