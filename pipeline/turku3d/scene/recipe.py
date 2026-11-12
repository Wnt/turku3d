"""Pinned producer inputs for a reproducible, bounded semantic scene release."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from turku3d.package.gltf import read_glb
from .compile import compile_scene, load_evidence, write_products
from .schema import vector
from .asset_store import resolve_asset

VERSION='turku-scene-recipe/1'
FIELDS={'version','id','bounds','inputs','assets','materialRecipes','trees','assetSources','fixtureBindings'}


def source_pins(cache, bounds):
    sources=load_evidence(Path(cache),bounds)[4]
    from .structures import source_records
    sources.extend(source for source,_ in source_records(cache,bounds))
    pins=[dict(path=row['id'],sha256=row['sha256']) for row in sources]
    retaining=Path(cache)/'retaining.pkl'
    if retaining.exists():pins.append(dict(path='retaining.pkl',sha256=hashlib.sha256(retaining.read_bytes()).hexdigest()))
    return sorted(pins,key=lambda row:row['path'])


def create_recipe(identity, bounds, cache, *, assets, material_recipes, trees, asset_sources, fixture_bindings):
    binding=None
    if fixture_bindings:
        file=Path(fixture_bindings).resolve()
        relative=file.relative_to(Path(__file__).parent.resolve())
        binding=dict(path=relative.as_posix(),sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    return dict(version=VERSION,id=identity,bounds=list(bounds),inputs=source_pins(cache,bounds),
                assets=assets,materialRecipes=material_recipes,trees=trees,assetSources=asset_sources,fixtureBindings=binding)


def validate_recipe(recipe):
    if not isinstance(recipe,dict) or set(recipe)!=FIELDS or recipe['version']!=VERSION:
        raise ValueError('Unsupported or incomplete scene recipe')
    vector(recipe['bounds'],4,'recipe bounds')
    x,z,X,Z=recipe['bounds']
    if not x<X or not z<Z:raise ValueError('Empty recipe bounds')
    if not isinstance(recipe['id'],str) or not recipe['id']:raise ValueError('Recipe requires an identity')
    for key in ('inputs','assets','materialRecipes','trees','assetSources'):
        if not isinstance(recipe[key],list):raise ValueError('Recipe '+key+' must be an array')
    pins=recipe['inputs']+([recipe['fixtureBindings']] if recipe['fixtureBindings'] else [])
    for pin in pins:
        if set(pin)!={'path','sha256'} or len(pin['sha256'])!=64:
            raise ValueError('Recipe inputs require a relative path and SHA-256')
        path=Path(pin['path'])
        if path.is_absolute() or '..' in path.parts:raise ValueError('Recipe input path escapes its source root')
    if len({p['path'] for p in recipe['inputs']})!=len(recipe['inputs']):raise ValueError('Duplicate recipe source pin')
    return recipe


def copy_assets(recipe, asset_root, output):
    root,output=Path(asset_root).expanduser().resolve(),Path(output).expanduser().resolve()
    copied=set()
    def copy(url,sha):
        source=(root/url).resolve();target=(output/url).resolve()
        if not source.is_relative_to(root) or not target.is_relative_to(output):raise ValueError('Asset dependency escapes release')
        source=resolve_asset(source)
        raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Pinned asset dependency hash mismatch: '+url)
        if target not in copied:
            target.parent.mkdir(parents=True,exist_ok=True)
            if source!=target:shutil.copyfile(source,target)
            copied.add(target)
        return raw
    for asset in recipe['assets']:
        raw=copy(asset['url'],asset['sha256']);document,_=read_glb(raw)
        for image in document.get('images',[]):
            if 'uri' in image:
                relative=str(Path(asset['url']).parent/image['uri'])
                copy(relative,Path(image['uri']).stem)
    for row in recipe['materialRecipes']:
        for key in ('baseColorTexture','normalTexture','ormTexture'):
            if key in row['material']:
                texture=row['material'][key];copy(texture['url'],texture['sha256'])
    return len(copied)


def build_recipe(recipe_path, cache, asset_root, output):
    raw=Path(recipe_path).read_bytes();recipe=validate_recipe(json.loads(raw))
    if source_pins(cache,recipe['bounds'])!=sorted(recipe['inputs'],key=lambda row:row['path']):
        raise ValueError('Cached evidence differs from pinned scene recipe')
    binding=recipe['fixtureBindings']
    path=Path(__file__).parent/binding['path'] if binding else None
    if binding and hashlib.sha256(path.read_bytes()).hexdigest()!=binding['sha256']:
        raise ValueError('Fixture associations differ from pinned scene recipe')
    copy_assets(recipe,asset_root,output)
    provenance=recipe['assetSources']+[dict(id=recipe['id']+':recipe',sha256=hashlib.sha256(raw).hexdigest(),kind='scene-recipe',uncertaintyM=0.)]
    scene=compile_scene(cache,bounds=recipe['bounds'],scene_id=recipe['id'],assets=recipe['assets'],
                        material_recipes=recipe['materialRecipes'],trees=recipe['trees'],asset_sources=provenance,
                        fixture_bindings=path)
    return write_products(scene,output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Pin producer inputs from an approved prepared scene')
    parser.add_argument('--scene',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--materials',type=Path,required=True)
    parser.add_argument('--trees',type=Path,required=True)
    parser.add_argument('--fixture-bindings',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();scene=json.loads(args.scene.read_text())
    generated={'ground','wfs','poles','walkable','roads','stairs','raw-ground-survey','retaining-survey','registered-reference-association','scene-recipe','compiled-ambient-visibility','compiled-street-irradiance'}
    recipe=create_recipe(scene['id'],scene['bounds'],args.cache,assets=scene['assets'],
                         material_recipes=json.loads(args.materials.read_text()),trees=json.loads(args.trees.read_text()),
                         asset_sources=[s for s in scene['sources'] if s['kind'] not in generated],fixture_bindings=args.fixture_bindings)
    validate_recipe(recipe);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(recipe,indent=2)+'\n')
