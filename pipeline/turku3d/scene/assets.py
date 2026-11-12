"""Freeze approved published building assets into the current scene's asset table.

This is an offline source adapter. The runtime never reads old tile manifests.
GLB shader attributes and every texture byte remain unchanged.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

from turku3d.package.gltf import read_glb


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
        data = urllib.request.urlopen(url, timeout=30).read()
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
                image_data = urllib.request.urlopen(urllib.parse.urljoin(url, uri), timeout=30).read()
                if Path(uri).stem != hashlib.sha256(image_data).hexdigest():
                    raise ValueError("Published image hash mismatch")
                target.write_bytes(image_data)
        assets.append(dict(id=placement["id"], entityId=placement["id"], url="shared/" + filename,
                           sha256=sha, position=position, recipe="building", bounds=dict(min=low,max=high)))
    return assets


def import_materials(glb_path, base_url, output):
    """Reuse published neutral PBR maps, baking their metric transforms into UVs."""
    document, binary = read_glb(Path(glb_path).read_bytes())
    output=Path(output); shared=output/'shared'; shared.mkdir(parents=True,exist_ok=True)
    aliases={'road':'draft_asphalt_worn','footway':'draft_concrete_slabs','quay':'draft_concrete_slabs',
             'quay-wall':'draft_quay_stone','ground':'draft_cobblestone'}
    records=[]
    for identity,library in aliases.items():
        material=next(m for m in document['materials'] if m.get('extras',{}).get('library_material_key')==library)
        pbr=material['pbrMetallicRoughness']
        row=dict(id=identity,color=pbr.get('baseColorFactor',[1,1,1,1])[:3],roughness=pbr.get('roughnessFactor',1),metalness=pbr.get('metallicFactor',1))
        scales=[]
        for name,info in [('baseColorTexture',pbr.get('baseColorTexture')),('normalTexture',material.get('normalTexture')),('ormTexture',pbr.get('metallicRoughnessTexture'))]:
            if info is None:continue
            texture=document['textures'][info['index']]
            image=document['images'][texture.get('source',texture.get('extensions',{}).get('KHR_texture_basisu',{}).get('source'))]
            if 'uri' in image:
                data=urllib.request.urlopen(urllib.parse.urljoin(base_url,image['uri']),timeout=30).read()
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
