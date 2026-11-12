"""Publish an appearance-only change to a compiled prepared scene.

This is the one appearance publication path. A patch replaces existing scene
materials (and their recipe rows) or the asset of an existing entity. Geometry,
supports, fixtures, emitters, baked lighting and collision products are reused
byte for byte; only mesh UVs of a material whose metric scale changes are
rescaled. The runtime applies the same patch live (window.__dev.scene.patch).

  python -m turku3d.scene.appearance --scene game/public/scenes/office.scene.json \
    --recipe pipeline/turku3d/scene/recipes/office.json --patch PATCH.json \
    [--catalog game/public/scenes/regions.json] [--asset-root DIR] [--output DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from copy import deepcopy
from pathlib import Path

from .asset_store import prepared_asset_root
from .recipe import copy_assets, validate_recipe
from .regions import validate_catalog
from .schema import fields, validate_asset, validate_material, validate_source, vector

SCHEMA = 'turku-scene-appearance/1'
# Scene keys an appearance change may replace. Everything else must stay identical.
MUTABLE = {'materials', 'assets', 'sources', 'release'}
ASSET_FIELDS = {'entityId', 'url', 'sha256', 'bounds'}
ASSET_URL = re.compile(r'shared/([0-9a-f]{64})\.[a-z0-9]+')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sorted_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def seal(parts):
    """release_hash() assembled from per-key sorted JSON, so unchanged keys are serialised once."""
    return digest(('{' + ','.join(json.dumps(k) + ':' + parts[k] for k in sorted(parts) if k != 'release') + '}').encode())


def validate_patch(patch, scene_id):
    if not isinstance(patch, dict) or set(patch) - {'schema', 'scene', 'materials', 'assets'} or patch.get('schema') != SCHEMA:
        raise ValueError('Unsupported appearance patch')
    if patch.get('scene') != scene_id:
        raise ValueError(f"Appearance patch targets {patch.get('scene')!r}, not {scene_id!r}")
    materials, assets = patch.get('materials', []), patch.get('assets', [])
    if not isinstance(materials, list) or not isinstance(assets, list) or not materials + assets:
        raise ValueError('Appearance patch changes nothing')
    for row in materials:
        if not isinstance(row, dict) or set(row) - {'material', 'uvScale'} or 'material' not in row:
            raise ValueError('Material rows need a complete material and an optional uvScale')
        validate_material(row['material'])
        if 'uvScale' in row:
            vector(row['uvScale'], 2, 'uvScale')
            if any(v <= 0 for v in row['uvScale']):
                raise ValueError('uvScale must be positive')
    for row in assets:
        if not isinstance(row, dict) or set(row) - ASSET_FIELDS or {'entityId', 'url', 'sha256'} - set(row):
            raise ValueError('Asset rows need entityId, url and sha256 (bounds optional)')
        match = ASSET_URL.fullmatch(str(row['url']))
        if not match or match[1] != row['sha256']:
            raise ValueError('Asset URL must be the content address shared/<sha256>.<ext>: ' + str(row['url']))
    for rows, key in ((materials, lambda r: r['material']['id']), (assets, lambda r: r['entityId'])):
        if len({key(r) for r in rows}) != len(rows):
            raise ValueError('Appearance patch names the same target twice')
    return patch


def check_appearance_only(before, after, rescaled):
    """Every scene key except materials/assets/sources/release is unchanged; meshes differ only in rescaled UVs."""
    if set(before) != set(after):
        raise AssertionError('Appearance change altered the scene fields')
    for key in before.keys() - MUTABLE - {'meshes'}:
        if after[key] is not before[key] and after[key] != before[key]:
            raise AssertionError('Appearance change altered ' + key)
    old, new = before['meshes'], after['meshes']
    if old is new:
        return
    if len(old) != len(new):
        raise AssertionError('Appearance change altered meshes')
    for a, b in zip(old, new, strict=True):
        if a is b:
            continue
        if a['materialId'] not in rescaled or {k: v for k, v in a.items() if k != 'uv'} != {k: v for k, v in b.items() if k != 'uv'}:
            raise AssertionError('Appearance change altered mesh ' + a['id'])


def apply_patch(scene, recipe, patch, asset_root=None):
    """Return (scene, recipe, receipt) built from shallow copies; the inputs are not mutated."""
    validate_patch(patch, scene['id'])
    if recipe['id'] != scene['id']:
        raise ValueError('Recipe belongs to another scene')
    out, recipe = dict(scene), deepcopy(recipe)
    out['materials'], out['assets'], out['sources'] = deepcopy(scene['materials']), deepcopy(scene['assets']), deepcopy(scene['sources'])
    materials = {m['id']: i for i, m in enumerate(out['materials'])}
    recipe_rows = {r['material']['id']: r for r in recipe['materialRecipes']}
    scales = {}
    for row in patch.get('materials', []):
        material = deepcopy(row['material'])
        if material['id'] not in materials:
            raise ValueError('Unknown scene material: ' + material['id'])
        out['materials'][materials[material['id']]] = material
        current = recipe_rows.get(material['id'])
        old = list(current['uvScale']) if current else [1, 1]
        new = list(row.get('uvScale', old))
        if current:
            current.update(material=deepcopy(material), uvScale=new)
        else:
            recipe['materialRecipes'].append(dict(material=deepcopy(material), uvScale=new))
        if new != old:
            scales[material['id']] = (new[0] / old[0], new[1] / old[1])
    rescaled = 0
    if scales:
        meshes = []
        for mesh in scene['meshes']:
            factor = scales.get(mesh['materialId'])
            if factor and 'uv' in mesh:
                uv = mesh['uv']
                mesh = {**mesh, 'uv': [v * factor[i % 2] for i, v in enumerate(uv)]}
                rescaled += 1
            meshes.append(mesh)
        out['meshes'] = meshes
    replaced = []
    for row in patch.get('assets', []):
        scene_asset = next((a for a in out['assets'] if a['entityId'] == row['entityId']), None)
        recipe_asset = next((a for a in recipe['assets'] if a['entityId'] == row['entityId']), None)
        if scene_asset is None or recipe_asset is None:
            raise ValueError('Unknown asset entity: ' + row['entityId'])
        old_sha = scene_asset['sha256']
        for asset in (scene_asset, recipe_asset):
            asset.update(deepcopy({k: row[k] for k in ASSET_FIELDS - {'entityId'} if k in row}))
            validate_asset(asset)
        if old_sha != row['sha256']:
            for source in [*out['sources'], *recipe['assetSources']]:
                if source['sha256'] == old_sha:
                    source['sha256'] = row['sha256']
        replaced.append(row['entityId'])
    # Every new texture and asset dependency must exist in the content store with its pinned hash.
    root = Path(asset_root) if asset_root else prepared_asset_root()
    copy_assets(dict(assets=[a for a in out['assets'] if a['entityId'] in replaced],
                     materialRecipes=patch.get('materials', [])), root, None)
    validate_recipe(recipe)
    recipe_raw = (json.dumps(recipe, indent=2) + '\n').encode()
    recipe_source = next((s for s in out['sources'] if s['id'] == scene['id'] + ':recipe'), None)
    if recipe_source is None:
        raise ValueError('Scene has no recipe provenance row')
    recipe_source['sha256'] = digest(recipe_raw)
    for source in out['sources']:
        validate_source(source)
    check_appearance_only(scene, out, set(scales))
    return out, recipe_raw, dict(materials=[r['material']['id'] for r in patch.get('materials', [])],
                                 assets=replaced, rescaledMeshes=rescaled)


def publish(scene_path, recipe_path, patch, *, catalog_path=None, asset_root=None, output=None):
    started = time.perf_counter()
    scene_path, recipe_path = Path(scene_path), Path(recipe_path)
    output = Path(output) if output else scene_path.parent
    scene = json.loads(scene_path.read_bytes())
    fields(scene, 'scene')
    recipe_raw = recipe_path.read_bytes()
    recipe = validate_recipe(json.loads(recipe_raw))
    parts = {k: sorted_json(v) for k, v in scene.items() if k != 'release'}
    if seal(parts) != scene['release']:
        raise ValueError('Source scene content differs from its sealed release')
    if not any(s['id'] == scene['id'] + ':recipe' and s['sha256'] == digest(recipe_raw) for s in scene['sources']):
        raise ValueError('Recipe does not identify this prepared scene')
    cells = {}
    for row in scene['collision'].values():
        raw = (scene_path.parent / row['url']).read_bytes()
        if digest(raw) != row['sha256'] or len(raw) != row['bytes']:
            raise ValueError('Collision product differs from the sealed scene: ' + row['url'])
        cells[row['url']] = raw
    read = time.perf_counter()
    before, meshes = scene['release'], scene['meshes']
    scene, recipe_raw, receipt = apply_patch(scene, recipe, patch, asset_root)
    for key in ('materials', 'assets', 'sources', *(('meshes',) if scene['meshes'] is not meshes else ())):
        parts[key] = sorted_json(scene[key])
    scene['release'] = seal(parts)
    catalog = None
    if catalog_path:
        catalog = json.loads(Path(catalog_path).read_text())
        row = next((r for r in catalog['regions'] if r['id'] == scene['id']), None)
        if row is None or row['release'] != before:
            raise ValueError('Catalog does not pin the source scene')
        row['release'] = scene['release']
        validate_catalog(catalog)
        if any(row[key] != scene[key] for key in ('bounds', 'spawn')):
            raise ValueError('Catalog descriptor differs from its pinned scene')
    applied = time.perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    if output.resolve() != scene_path.parent.resolve():
        for name, raw in cells.items():
            (output / name).write_bytes(raw)
    (output / scene_path.name).write_text(json.dumps(scene, separators=(',', ':')) + '\n')
    if output.resolve() == scene_path.parent.resolve():
        recipe_path.write_bytes(recipe_raw)
        if catalog_path:
            Path(catalog_path).write_text(json.dumps(catalog, indent=2) + '\n')
    else:
        (output / recipe_path.name).write_bytes(recipe_raw)
        if catalog_path:
            (output / Path(catalog_path).name).write_text(json.dumps(catalog, indent=2) + '\n')
    done = time.perf_counter()
    return dict(scene=scene['id'], before=before, after=scene['release'], **receipt,
                collisionCellsReused=len(cells),
                timings=dict(readMs=ms(started, read), applyMs=ms(read, applied), writeMs=ms(applied, done), totalMs=ms(started, done)))


def ms(start, end):
    return round((end - start) * 1000)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, required=True)
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--asset-root', type=Path, help='Directory containing shared/ (default: the prepared asset store)')
    parser.add_argument('--output', type=Path, help='Write here instead of publishing in place')
    args = parser.parse_args(argv)
    receipt = publish(args.scene, args.recipe, json.loads(args.patch.read_text()), catalog_path=args.catalog,
                      asset_root=args.asset_root, output=args.output)
    print(json.dumps(receipt))
    return receipt


if __name__ == '__main__':
    main()
