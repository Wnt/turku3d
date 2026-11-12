"""Rebind one qualified appearance input without rebuilding unaffected ground stages."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from turku3d.package.gltf import read_glb
from .asset_store import resolve_asset
from .compile import release_hash
from .recipe import copy_assets, validate_recipe
from .schema import validate_scene


def publish_input(scene_path, recipe_path, asset_path, output, output_recipe):
    scene_path, recipe_path, output = Path(scene_path), Path(recipe_path), Path(output)
    raw = recipe_path.read_bytes(); recipe = validate_recipe(json.loads(raw))
    scene = validate_scene(json.loads(scene_path.read_text())); before = deepcopy(scene)
    if release_hash(scene) != scene['release']:
        raise ValueError('Source scene identity differs')
    recipe_source = next(s for s in scene['sources'] if s['id']==scene['id']+':recipe')
    if recipe_source['sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('Source recipe does not identify this prepared scene')
    data_raw = Path(__file__).with_name('qwensel_roof.json').read_bytes()
    data = json.loads(data_raw); data_hash = hashlib.sha256(data_raw).hexdigest()
    asset_raw = resolve_asset(asset_path).read_bytes()
    target = hashlib.sha256(asset_raw).hexdigest(); document, binary = read_glb(asset_raw)
    _, source_binary = read_glb(resolve_asset(data['sourceGlbSha256']+'.glb').read_bytes())
    if binary[:len(source_binary)] != source_binary:
        raise ValueError('Roof input changed the approved binary prefix')
    evidence = document['asset']['extras']['qwenselRoof']
    if evidence['sourceSha256'] != data['sourceGlbSha256'] or evidence['roofDataSha256'] != data_hash:
        raise ValueError('Appearance input lacks the qualified roof source')
    entity = '103295888D'
    old = next(a for a in scene['assets'] if a['entityId']==entity)
    new = next(a for a in recipe['assets'] if a['entityId']==entity)
    if old != new or old['sha256'] != data['sourceGlbSha256']:
        raise ValueError('Prepared and recipe appearance inputs differ')
    new.update(url='shared/'+target+'.glb', sha256=target)
    new['bounds']['max'][1] = max(c['topM'] for c in data['chimneys'])
    old.update(deepcopy(new))
    source_id = 'building:'+entity+':frontage'
    for rows in (scene['sources'], recipe['assetSources']):
        row = next(s for s in rows if s['id']==source_id)
        if row['kind']!='registered-frontage-appearance' or row['sha256']!=data['sourceGlbSha256']:
            raise ValueError('Approved frontage authority differs')
        row['sha256'] = target
    roof = dict(id='building:'+entity+':roof-detail', sha256=data_hash,
                kind='registered-roof-detail', uncertaintyM=.15)
    if any(s['id']==roof['id'] for s in scene['sources']):
        raise ValueError('Roof appearance already installed')
    recipe['assetSources'].append(roof)
    scene['sources'].insert(scene['sources'].index(recipe_source), deepcopy(roof))
    recipe_raw = (json.dumps(recipe, indent=2)+'\n').encode()
    recipe_source['sha256'] = hashlib.sha256(recipe_raw).hexdigest()
    for key in scene.keys()-{'assets','sources','release'}:
        if scene[key] != before[key]:
            raise AssertionError('Appearance stage changed '+key)
    copy_assets(recipe, Path(asset_path).expanduser().resolve().parent.parent, output)
    for row in scene['collision'].values():
        raw = (scene_path.parent/row['url']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:
            raise ValueError('Source collision changed')
        (output/row['url']).write_bytes(raw)
    scene['release'] = release_hash(scene); validate_scene(scene)
    Path(output_recipe).write_bytes(recipe_raw)
    (output/(scene['id']+'.scene.json')).write_text(json.dumps(scene,separators=(',',':'))+'\n')
    return dict(before=before['release'], after=scene['release'], asset=target,
                preservedNativeMeshes=len(scene['meshes']), collisionCellsExact=len(scene['collision']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('scene','recipe','asset','output','output-recipe'):
        p.add_argument('--'+name, type=Path, required=True)
    a=p.parse_args()
    print(json.dumps(publish_input(a.scene,a.recipe,a.asset,a.output,a.output_recipe)))
