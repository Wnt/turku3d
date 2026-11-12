"""Refresh a pinned scene after ground changes without losing source tree identity."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shapely
from turku3d.package.gltf import read_glb
from .assets import import_vegetation
from .compile import compile_scene,load_evidence
from .recipe import build_recipe,copy_assets,source_pins,validate_recipe


def resupport_recipe(recipe_path,cache,asset_root,output,recipe_output):
    recipe_path,cache,asset_root,output,recipe_output=map(Path,(recipe_path,cache,asset_root,output,recipe_output))
    recipe=validate_recipe(json.loads(recipe_path.read_text()))
    copy_assets(recipe,asset_root,output)
    binding=recipe['fixtureBindings'];binding_path=Path(__file__).parent/binding['path'] if binding else None
    if binding and hashlib.sha256(binding_path.read_bytes()).hexdigest()!=binding['sha256']:
        raise ValueError('Fixture binding changed independently of the pinned recipe')
    recipe['inputs']=source_pins(cache,recipe['bounds'])
    support_scene=compile_scene(cache,bounds=recipe['bounds'],scene_id=recipe['id'],spawn=recipe['spawn'],assets=recipe['assets'],
                                material_recipes=recipe['materialRecipes'],trees=[],asset_sources=recipe['assetSources'],
                                fixture_bindings=binding_path)
    water=shapely.union_all(load_evidence(cache,recipe['bounds'])[3])
    expected={t['id'] for t in recipe['trees']};anchors={};updated=[]
    for index,asset in enumerate(recipe['assets']):
        if asset['recipe']!='vegetation':continue
        path=(output/asset['url']).resolve();document,_=read_glb(path.read_bytes())
        identities={identity for node in document.get('nodes',[]) for identity in node.get('extras',{}).get('instance_object_ids',[])}
        if not identities&expected:continue
        product,trees=import_vegetation(path,path.as_uri(),output,water,support_scene,asset_id=asset['id'])
        for tree in trees:
            if tree['id'] not in expected:continue
            if tree['id'] in anchors:raise ValueError('Tree anchor appears in multiple prepared assets: '+tree['id'])
            anchors[tree['id']]=tree
        recipe['assets'][index]=product
        if product!=asset:updated.append(asset['id'])
    if set(anchors)!=expected:raise ValueError('Prepared vegetation does not retain every pinned tree anchor')
    recipe['trees']=[anchors[t['id']] for t in recipe['trees']]
    recipe_output.parent.mkdir(parents=True,exist_ok=True)
    recipe_output.write_text(json.dumps(recipe,indent=2)+'\n')
    scene=build_recipe(recipe_output,cache,output,output)
    return dict(release=scene['release'],updatedVegetationAssets=updated,treeAnchors=len(anchors),
                solidTreeTrunks=sum(f['family']=='published-tree-trunk' for f in scene['fixtures']))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('recipe','cache','asset-root','output','recipe-output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(resupport_recipe(args.recipe,args.cache,args.asset_root,args.output,args.recipe_output),indent=2))
