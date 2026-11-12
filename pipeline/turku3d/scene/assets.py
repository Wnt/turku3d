"""Freeze approved published building assets into the current scene's asset table.

This is an offline source adapter. The runtime never reads old tile manifests.
GLB shader attributes and every texture byte remain unchanged.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import urllib.parse

from turku3d.package.gltf import read_glb
from .asset_store import resolve_asset, read_asset_url


# Response follows the explicitly selected library asset, never a scene material ID.
# Unlisted/porous finishes retain their authored PBR until a suitable response exists.
LIBRARY_SURFACES = {
    'draft_asphalt_worn': 'asphalt',
    'draft_concrete_slabs': 'stone', 'draft_concrete_pavers': 'stone',
    'draft_quay_stone': 'stone', 'draft_fieldstone': 'stone',
    'draft_brick_paving': 'stone', 'draft_granite_curb': 'granite',
    'draft_cobblestone': 'cobblestone',
}


def import_buildings(manifest, base_url, output, bounds):
    output = Path(output)
    shared = output / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    source = manifest["buildingPlacements"]
    models = {m["id"]: m for m in source["models"]}
    assets = []
    for placement in source["placements"]:
        model = models[placement["model"]]
        position = placement["position"]
        low = [a+b for a,b in zip(model["bounds"]["min"],position,strict=True)]
        high = [a+b for a,b in zip(model["bounds"]["max"],position,strict=True)]
        if high[0] < bounds[0] or low[0] > bounds[2] or high[2] < bounds[1] or low[2] > bounds[3]:
            continue
        url = urllib.parse.urljoin(base_url, model["uri"])
        data = read_asset_url(url)
        sha = hashlib.sha256(data).hexdigest()
        filename = sha + ".glb"
        (shared / filename).write_bytes(data)
        document, _ = read_glb(data)
        for image in document.get("images", []):
            uri = image.get("uri")
            if not uri:
                continue
            # Retain the asset's authored relative layout within our own output.
            target = (shared / uri).resolve()
            if not target.is_relative_to(output.resolve()):
                raise ValueError("Asset dependency escapes scene root")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                image_data = read_asset_url(urllib.parse.urljoin(url, uri))
                if Path(uri).stem != hashlib.sha256(image_data).hexdigest():
                    raise ValueError("Published image hash mismatch")
                target.write_bytes(image_data)
        assets.append(dict(id=placement["id"], entityId=placement["id"], url="shared/" + filename,
                           sha256=sha, position=position, recipe="building", bounds=dict(min=low,max=high)))
    return assets


def import_materials(glb_path, base_url, output):
    """Reuse published neutral PBR maps, baking their metric transforms into UVs."""
    document, binary = read_glb(resolve_asset(glb_path).read_bytes())
    output=Path(output); shared=output/'shared'; shared.mkdir(parents=True,exist_ok=True)
    aliases={'road':'draft_asphalt_worn','footway':'draft_concrete_slabs','quay':'draft_concrete_slabs',
             'quay-wall':'draft_quay_stone','ground':'draft_cobblestone','cobblestone':'draft_cobblestone'}
    records=[]
    for identity,library in aliases.items():
        material=next(m for m in document['materials'] if m.get('extras',{}).get('library_material_key')==library)
        pbr=material['pbrMetallicRoughness']
        row=dict(id=identity,color=pbr.get('baseColorFactor',[1,1,1,1])[:3],roughness=pbr.get('roughnessFactor',1),metalness=pbr.get('metallicFactor',1))
        if library in LIBRARY_SURFACES:row["surface"]=LIBRARY_SURFACES[library]
        scales=[]
        for name,info in [('baseColorTexture',pbr.get('baseColorTexture')),('normalTexture',material.get('normalTexture')),('ormTexture',pbr.get('metallicRoughnessTexture'))]:
            if info is None:continue
            texture=document['textures'][info['index']]
            image=document['images'][texture.get('source',texture.get('extensions',{}).get('KHR_texture_basisu',{}).get('source'))]
            if 'uri' in image:
                data=read_asset_url(urllib.parse.urljoin(base_url,image['uri']))
            else:
                view=document['bufferViews'][image['bufferView']]
                data=binary[view.get('byteOffset',0):view.get('byteOffset',0)+view['byteLength']]
            sha=hashlib.sha256(data).hexdigest(); filename=sha+'.ktx2';(shared/filename).write_bytes(data)
            row[name]=dict(url='shared/'+filename,sha256=sha,wrap='repeat')
            transform=info.get('extensions',{}).get('KHR_texture_transform',{})
            if set(transform)-{'scale'}:raise ValueError('Material transform needs explicit adapter')
            scales.append(transform.get('scale',[1,1]))
        if any(scale!=scales[0] for scale in scales):raise ValueError('PBR maps require different UV sets')
        records.append(dict(material=row,uvScale=scales[0]))
    return records


def import_vegetation(glb_path, base_url, output, water, support_scene=None, *, asset_id):
    """Keep explicit prepared tree instances and material attributes from one source tile."""
    import copy
    import struct
    import subprocess
    import shapely
    import numpy as np
    import tempfile
    from .asset_buffers import embedded_buffers, encode_glb, node_world_matrices
    document,binary=read_glb(resolve_asset(glb_path).read_bytes())
    document,binary=embedded_buffers(document,binary,base_url)
    worlds=node_world_matrices(document)
    with tempfile.NamedTemporaryFile(suffix='.glb') as prepared:
        prepared.write(encode_glb(document,binary));prepared.flush()
        transforms=json.loads(subprocess.check_output(['node',str(Path(__file__).with_name('decode_instances.mjs')),prepared.name]))
    selected=[i for i,n in enumerate(document['nodes']) if n.get('extras',{}).get('layer')=='vegetation']
    result=copy.deepcopy(document)
    result['nodes']=[copy.deepcopy(document['nodes'][i]) for i in selected]
    result['scenes']=[dict(nodes=list(range(len(selected))))];result['scene']=0
    mesh_ids=sorted({n['mesh'] for n in result['nodes']});mesh_map={old:new for new,old in enumerate(mesh_ids)}
    result['meshes']=[copy.deepcopy(document['meshes'][i]) for i in mesh_ids]
    for original,node in zip(selected,result['nodes'],strict=True):
        node['mesh']=mesh_map[node['mesh']]
        for field in ('translation','rotation','scale','children'):node.pop(field,None)
        node['matrix']=worlds[original].T.ravel().tolist()
    material_ids=sorted({p['material'] for m in result['meshes'] for p in m['primitives']})
    material_map={old:new for new,old in enumerate(material_ids)}
    result['materials']=[copy.deepcopy(document['materials'][i]) for i in material_ids]
    for m in result['meshes']:
        for primitive in m['primitives']:primitive['material']=material_map[primitive['material']]
    texture_infos=[]
    def visit(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.endswith('Texture') and isinstance(item,dict) and 'index' in item:texture_infos.append(item)
                else:visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    visit(result['materials'])
    texture_ids=sorted({v['index'] for v in texture_infos});texture_map={old:new for new,old in enumerate(texture_ids)}
    result['textures']=[copy.deepcopy(document['textures'][i]) for i in texture_ids]
    for info in texture_infos:info['index']=texture_map[info['index']]
    image_ids=set()
    for texture in result['textures']:
        if 'source' in texture:image_ids.add(texture['source'])
        for extension in texture.get('extensions',{}).values():
            if 'source' in extension:image_ids.add(extension['source'])
    image_ids=sorted(image_ids);image_map={old:new for new,old in enumerate(image_ids)}
    result['images']=[copy.deepcopy(document['images'][i]) for i in image_ids]
    for texture in result['textures']:
        if 'source' in texture:texture['source']=image_map[texture['source']]
        for extension in texture.get('extensions',{}).values():
            if 'source' in extension:extension['source']=image_map[extension['source']]
    from .fixtures import support_query
    query=support_query(support_scene) if support_scene else lambda x,z,reference_y=None:None
    trees={}
    for original,node in zip(selected,result['nodes'],strict=True):
        values=transforms.get(str(original))
        if not values:continue
        positions=np.asarray(values['TRANSLATION'],float);extras=node['extras']
        world=(np.c_[positions,np.ones(len(positions))]@worlds[original].T)[:,:3]
        for position in world:
            support=query(position[0],position[2],reference_y=position[1])
            if support is not None:position[1]=support[1]
        positions=(np.c_[world,np.ones(len(world))]@np.linalg.inv(worlds[original]).T)[:,:3]
        # Replace only the authored translations with supported prepared values;
        # every mesh, crown, rotation and scale remains the approved source data.
        import numpy as np
        prepared=np.asarray(positions,dtype='<f4')
        if not np.array_equal(prepared,np.asarray(values['TRANSLATION'],dtype='<f4')):
            raw=prepared.tobytes()
            offset=len(binary);binary+=raw
            vi=len(result['bufferViews']);result['bufferViews'].append(dict(buffer=0,byteOffset=offset,byteLength=len(raw)))
            ai=len(result['accessors']);result['accessors'].append(dict(bufferView=vi,componentType=5126,count=len(positions),type='VEC3'))
            node['extensions']['EXT_mesh_gpu_instancing']['attributes']['TRANSLATION']=ai
            result['buffers'][0]['byteLength']=len(binary)
        extras['instance_near_water']=[shapely.Point(p[0],p[2]).distance(water)<25 for p in world]
        identities=extras['instance_object_ids']
        for k,(identity,position) in enumerate(zip(identities,world.tolist(),strict=True)):
            scale=(np.asarray(values.get('SCALE',[[1,1,1]]*len(positions))[k])*np.linalg.norm(worlds[original][:3,:3],axis=0)).tolist()
            trees.setdefault(identity,dict(id=identity,position=position,scale=scale))
    shared=Path(output)/'shared';shared.mkdir(parents=True,exist_ok=True)
    for image in result['images']:
        if 'uri' in image:
            target=(shared/image['uri']).resolve()
            if not target.is_relative_to(Path(output).resolve()):raise ValueError('Escaping vegetation image')
            if not target.exists():
                raw=read_asset_url(urllib.parse.urljoin(base_url,image['uri']))
                if Path(image['uri']).stem!=hashlib.sha256(raw).hexdigest():raise ValueError('Vegetation image hash mismatch')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    encoded=json.dumps(result,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
    data=struct.pack('<III',0x46546c67,2,12+8+len(encoded)+8+len(binary))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(binary),0x004e4942)+binary
    sha=hashlib.sha256(data).hexdigest();(shared/(sha+'.glb')).write_bytes(data)
    # Instance envelope includes each original prototype's scaled bounding box.
    lows=[];highs=[]
    for original,node in zip(selected,result['nodes'],strict=True):
        values=transforms[str(original)];matrix=worlds[original]
        mesh=result['meshes'][node['mesh']]
        for primitive in mesh['primitives']:
            accessor=result['accessors'][primitive['attributes']['POSITION']]
            lo=np.asarray(accessor['min']);hi=np.asarray(accessor['max'])
            radius=float(np.linalg.norm(np.maximum(abs(lo),abs(hi))))
            for identity in node['extras']['instance_object_ids']:
                tree=trees[identity];reach=radius*max(tree['scale']);position=np.asarray(tree['position']);lows.append(position-reach);highs.append(position+reach)
    asset=dict(id=asset_id,entityId=asset_id,url='shared/'+sha+'.glb',sha256=sha,position=[0,0,0],recipe='vegetation',bounds=dict(min=np.min(lows,axis=0).tolist(),max=np.max(highs,axis=0).tolist()))
    return asset,list(trees.values())
