"""Fit retained volumetric leaf modules to measured hedge solids.

Original solids remain authoritative for contact. Their flat rendering is replaced
by the retained leaf-card/core grammar. Only its actual opaque inner faces block
light, so the broader walking hull cannot enclose and blacken the visible leaves.
Species and sprig positions remain visual priors within the measured envelope.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np

REPO = Path(__file__).parents[3]
NATURE = 'f23c56adde4561c589bed710b67517d6093a4ed0a7649ca75c0850035e0d843a'
ATLAS = '9f80b7f396fd615e87fb1b2ddad8a3d644e5ba87e32291a49ce1c90f167541dc'
MATERIAL = dict(id='hedge-leaves', color=[1., 1., 1.], roughness=.75,
                metalness=0., alphaTest=.5, doubleSided=True,
                baseColorTexture=dict(url='shared/'+ATLAS+'.ktx2', sha256=ATLAS, wrap='clamp'))


@lru_cache(maxsize=1)
def templates():
    result = subprocess.run(['node', str(Path(__file__).with_name('hedge_templates.mjs')), str(REPO)],
                            capture_output=True, text=True, check=True, timeout=15)
    return json.loads(result.stdout)


def bilinear(corners, u, w):
    return ((1-u)*(1-w))[:, None]*corners[0]+(u*(1-w))[:, None]*corners[1]+(u*w)[:, None]*corners[2]+((1-u)*w)[:, None]*corners[3]


def geometry(core, identity, connected=(False, False)):
    """Warp one source module; normals use the inverse local deformation Jacobian."""
    seed = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], 'little')
    source = templates()[seed % len(templates())]
    p = np.asarray(source['positions']).reshape(-1, 3)
    lo, hi = np.asarray(source['bounds']['min']), np.asarray(source['bounds']['max'])
    t = (p-lo)/(hi-lo);_, v, w = t.T
    # Nominal core endpoints meet at each one-metre source segment. Sprigs
    # overlap adjacent modules; only true line ends/openings trim their reach.
    u = (p[:, 0]+1)/2
    if not connected[0]:u = np.maximum(u, 0)
    if not connected[1]:u = np.minimum(u, 1)
    f = np.asarray(core).reshape(6, 4, 3)
    feet = np.array([f[0, 0], f[0, 1], f[1, 1], f[2, 1]])
    tops = np.array([f[0, 3], f[0, 2], f[1, 2], f[2, 2]])
    # Keep longitudinal endpoints exact, including real path openings. A small
    # lateral/top shell breaks the planar silhouette inside source uncertainty.
    side = feet[3]-feet[0];side[1] = 0;side /= np.linalg.norm(side)
    for i, sign in enumerate([-1, -1, 1, 1]):
        feet[i] += side*sign*.05;tops[i] += side*sign*.05
    tops[:, 1] += .05
    bottom, top = bilinear(feet, u, w), bilinear(tops, u, w)
    result = bottom*(1-v[:, None])+top*v[:, None]
    du_bottom = (1-w[:, None])*(feet[1]-feet[0])+w[:, None]*(feet[2]-feet[3])
    du_top = (1-w[:, None])*(tops[1]-tops[0])+w[:, None]*(tops[2]-tops[3])
    dw_bottom = (1-u[:, None])*(feet[3]-feet[0])+u[:, None]*(feet[2]-feet[1])
    dw_top = (1-u[:, None])*(tops[3]-tops[0])+u[:, None]*(tops[2]-tops[1])
    jacobian = np.stack([(du_bottom*(1-v[:, None])+du_top*v[:, None])/2,
                         (top-bottom)/(hi[1]-lo[1]),
                         (dw_bottom*(1-v[:, None])+dw_top*v[:, None])/(hi[2]-lo[2])], axis=2)
    normals = np.einsum('nij,nj->ni', np.linalg.inv(jacobian).transpose(0, 2, 1), np.asarray(source['normals']).reshape(-1, 3))
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    return result, np.asarray(source['indices']).reshape(-1, 3), np.asarray(source['uv']).reshape(-1, 2), normals


def append(scene):
    from turku3d.scene.compile import mesh_record
    if any(m['materialId'] == MATERIAL['id'] for m in scene['meshes']):
        raise ValueError('Hedge leaf volumes are already compiled')
    leaves = [];proxies = [];endpoints = {}
    def end_keys(core):
        f = np.asarray(core['positions']).reshape(6, 6, 3)[:, [0, 2, 1, 4]]
        return tuple((core['entityId'], *np.round(point[[0, 2]], 4)) for point in [(f[0, 0]+f[2, 1])/2, (f[0, 1]+f[1, 1])/2])
    for core in scene['meshes']:
        if core['materialId'] == 'hedge':
            for key in end_keys(core):endpoints[key] = endpoints.get(key, 0)+1
    for core in scene['meshes']:
        if core['materialId'] != 'hedge':
            continue
        if core['collision'] != 'solid' or len(core['positions']) != 108:
            raise ValueError('Leaf volume requires the complete measured six-face solid')
        faces = np.asarray(core['positions']).reshape(6, 6, 3)[:, [0, 2, 1, 4]]
        p, ix, uv, normals = geometry(faces, core['id'], tuple(endpoints[k]>1 for k in end_keys(core)))
        keep = np.linalg.norm(np.cross(p[ix[:, 1]]-p[ix[:, 0]], p[ix[:, 2]]-p[ix[:, 0]]), axis=1) >= 2e-6
        record = mesh_record(core['id']+':leaves', core['entityId'], MATERIAL['id'], p, ix,
                             policy='passable', support=core['supportId'], uv=uv)
        record['normals'] = normals[ix[keep]].reshape(-1).tolist()
        # The retained template starts with its three opaque inner-core faces.
        # Reuse those exact deformed triangles as optical geometry, independently
        # of the measured broad walking hull. No light multiplier is introduced.
        proxy = mesh_record(core['id']+':light-core', core['entityId'], MATERIAL['id'], p, ix[:6],
                            policy='passable', support=core['supportId'], uv=uv)
        proxy.update(render=False, lightOcclusion='solid')
        proxies.append(proxy);leaves.append(record)
        core.update(render=False, lightOcclusion='none')
        core.pop('bakedVisibility', None);core.pop('nightIrradiance', None)
    if leaves:
        scene['meshes'].extend(leaves+proxies);scene['materials'].append(MATERIAL.copy());scene['sources'].extend(provenance())
    return {m['id'] for m in leaves}


def provenance():
    inputs = [Path(__file__), Path(__file__).with_name('hedge_templates.mjs'), REPO/'game/scripts/nature/trees.mjs']
    return [dict(id='hedge:retained-nature-foliage', sha256=ATLAS,
                 kind='retained-CC0-foliage-atlas', uncertaintyM=.15),
            dict(id='hedge:leaf-volume-producer', sha256=hashlib.sha256(b''.join(p.read_bytes() for p in inputs)).hexdigest(),
                 kind='procedural-appearance-prior', uncertaintyM=.05)]


def write_texture(scene, output):
    if any(m['materialId'] == MATERIAL['id'] for m in scene['meshes']):
        ensure_atlas(output)


def ensure_atlas(output):
    """Extract the existing compressed payload; never resample/repaint source images."""
    from turku3d.package.gltf import read_glb
    from turku3d.scene.asset_store import resolve_asset
    target = Path(output)/MATERIAL['baseColorTexture']['url']
    try:
        source = resolve_asset(target)
        raw = source.read_bytes()
    except FileNotFoundError:
        source = Path(__file__).parents[3]/'game/public/assets/nature/nature.glb'
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != NATURE:
            raise ValueError('Retained nature library changed; qualify the new foliage atlas')
        document, binary = read_glb(data)
        image = next(i for i in document['images'] if i.get('name') == 'nature_foliage')
        view = document['bufferViews'][image['bufferView']]
        raw = binary[view.get('byteOffset', 0):view.get('byteOffset', 0)+view['byteLength']]
    if hashlib.sha256(raw).hexdigest() != ATLAS:
        raise ValueError('Hedge foliage atlas hash mismatch')
    target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(raw)

