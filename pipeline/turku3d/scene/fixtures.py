"""Resolve mapped furniture against final semantic supports before emitting it."""
import numpy as np
import shapely
from .families import promenade_bench, litter_bin, PROMENADE_BENCH, LITTER_BIN
from .compile import mesh_record


def support_query(scene):
    surfaces = []
    by_id = {m['id']:m for m in scene['meshes']}
    for support in scene['supports']:
        mesh = by_id[support['meshId']]
        p = np.asarray(mesh['positions']).reshape(-1,3)
        ix = np.asarray(mesh['indices']).reshape(-1,3)
        xy = p[ix][:,:,[0,2]]
        polygons = shapely.polygons(xy)
        surfaces.append((support, p[ix], polygons))
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
    return support_at


def place_furniture(scene, props):
    support_at = support_query(scene)
    source_ids = [s['id'] for s in scene['sources'] if s['kind']=='poles']
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


def place_trunks(scene, trees):
    from .families import _Geometry
    support_at=support_query(scene)
    source_ids=[s['id'] for s in scene['sources'] if s['kind']=='ground']
    for tree in trees:
        if tree['id'].startswith('bush:'):
            continue
        x,y,z=tree['position'];hit=support_at(x,z)
        if hit is None:
            continue
        support,base=hit
        if abs(y-base)>.002:
            raise ValueError(f"Tree {tree['id']} was not attached to its final support")
        height=min(tree['scale'][1]*.55,8.)
        radius=float(np.clip(tree['scale'][1]*.018,.09,.55))
        g=_Geometry();g.taper(0,height,radius,radius,12)
        p=np.array(g.positions).reshape(-1,3)+[x,y,z]
        key=tree['id']+':trunk'
        m=mesh_record(key,tree['id'],'tree-trunk',p,np.array(g.indices).reshape(-1,3),support=support['id']);m['render']=False
        scene['meshes'].append(m)
        scene['fixtures'].append(dict(id=tree['id'],family='published-tree-trunk',supportId=support['id'],position=[x,y,z],yaw=0.,dimensions=[radius*2,height,radius*2],meshIds=[key],emitterIds=[],sourceIds=source_ids,uncertaintyM=.15))


def place_lamps(scene, props):
    """Compile only explicitly associated source poles; never classify by proximity."""
    import hashlib
    import json
    from pathlib import Path
    from .families import flat_canopy_lamp, crook_lamp, FLAT_CANOPY_LAMP, CROOK_LAMP
    raw=Path(__file__).with_name('office_fixture_bindings.json').read_bytes()
    bindings=json.loads(raw)
    source_id='office-fixture-family-bindings'
    scene['sources'].append(dict(id=source_id,sha256=hashlib.sha256(raw).hexdigest(),kind='registered-reference-association',uncertaintyM=1.))
    sources=[s['id'] for s in scene['sources'] if s['kind']=='poles']+[source_id]
    associations={row['sourceId']:row for row in bindings['lamps']}
    families={'flat_canopy':(flat_canopy_lamp,FLAT_CANOPY_LAMP),'crook':(crook_lamp,CROOK_LAMP)}
    support_at=support_query(scene)
    for prop in props:
        binding=associations.get(prop.object_id.split(':')[-1])
        if prop.kind!='lamp' or binding is None:
            continue
        hit=support_at(prop.x,prop.z)
        if hit is None:
            continue
        support,y=hit
        make,family=families[binding['family']]
        radius=family['supportRadiusM']
        foot=[support_at(prop.x+radius*np.cos(a),prop.z+radius*np.sin(a)) for a in np.linspace(0,2*np.pi,8,endpoint=False)]
        if any(p is None for p in foot) or max(abs(p[1]-y) for p in foot)>.06:
            raise ValueError(f"Lamp {prop.object_id} has no coherent measured contact footprint")
        recipe=bindings['families'][binding['family']];yaw=recipe['yaw']
        position=[prop.x,y,prop.z];ids=[]
        for piece in make(position,yaw):
            key=prop.object_id+':'+piece['id']
            vertices=np.array(piece['positions']).reshape(-1,3)
            # The declared contact disc meets the actual support plane. Only its
            # bottom ring changes; shaft, head and emitter remain rigid and upright.
            contact=np.isclose(vertices[:,1],y,atol=1e-7,rtol=0)
            for index in np.flatnonzero(contact):
                vertices[index,1]=support_at(vertices[index,0],vertices[index,2])[1]
            record=mesh_record(key,prop.object_id,piece['materialId'],vertices,
                               np.array(piece['indices']).reshape(-1,3),piece['collision'],support['id'])
            scene['meshes'].append(record);ids.append(key)
        x,h,z=family['emitterOffset'];c,s=np.cos(yaw),np.sin(yaw)
        emitter_id=prop.object_id+':light'
        scene['emitters'].append(dict(id=emitter_id,fixtureId=prop.object_id,position=[prop.x+c*x+s*z,y+h,prop.z-s*x+c*z],
                                      color=recipe['color'],intensity=recipe['intensity'],range=recipe['range'],category=recipe['category']))
        scene['fixtures'].append(dict(id=prop.object_id,family=family['family'],supportId=support['id'],position=position,
                                      yaw=yaw,dimensions=family['dimensions'],meshIds=ids,emitterIds=[emitter_id],
                                      sourceIds=sources,uncertaintyM=family['heightUncertaintyM']))
