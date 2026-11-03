"""Model ownership, immutable placement transforms and per-building rebuild costs."""
import json
import os
from pathlib import Path
import subprocess
import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse.archetypes import partition, publish, view_distance
from turku3d.coarse.inputs import Building
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh, read_glb


def fixture(tile):
    origin = np.asarray(tile.origin)
    buildings, meshes = [], []
    # One complete building crosses the tile's east edge; the other stays inside.
    for key, x, z in [('crossing', 245, 100), ('inside', 30, 40)]:
        positions = np.array([[x-10, 12, z-10], [x+10, 12, z-10], [x+10, 12, z+10], [x-10, 12, z+10]], dtype=float)
        world = positions + origin
        building = Building(key, shapely.Polygon(world[:, [0, 2]]), 0, 12, 'plaster')
        buildings.append((building, None))
        meshes.append(GlbMesh(key, positions, np.array([[0,2,1],[0,3,2]], dtype=np.uint32),
            extras=dict(layer='buildings', building_id=key, object_id=key, surface='roof', material_key='plaster')))
    terrain = GlbMesh('ground', np.array([[0,0,0],[250,0,0],[250,0,250],[0,0,250]], dtype=float),
        np.array([[0,2,1],[0,3,2]], dtype=np.uint32), extras=dict(layer='ground', object_id='ground', material_key='draft_soil'))
    return buildings, [terrain, *meshes]


def test_partition_keeps_whole_shapes_and_component_metadata():
    tile = C.VisualTile(0, -3, 2)
    shells, meshes = fixture(tile)
    meshes[1].baked_visibility = np.array([[.2,.5,1.]] * 4)
    meshes[1].extras['component_box'] = dict(center=[245,12,100], size=[20,1,20], tangent=[1,0,0])
    terrain, models = partition(tile, meshes, shells)
    assert terrain == [meshes[0]]
    assert len(models) == 2
    for key, origin, local, _ in models:
        original = next(mesh for mesh in meshes if mesh.name == key)
        np.testing.assert_array_equal(local[0].positions + origin, original.positions + tile.origin)
        np.testing.assert_array_equal(local[0].indices, original.indices)
    crossing = next(model for model in models if model[0] == 'crossing')
    assert max(crossing[2][0].positions[:, 0] + crossing[1][0]) > tile.bbox.xmax
    assert crossing[2][0].extras['component_box']['center'] == [0,12,0]
    np.testing.assert_array_equal(crossing[2][0].baked_visibility, meshes[1].baked_visibility)
    assert meshes[1].extras['component_box']['center'] == [245,12,100]
    assert view_distance([[-1,0,-1],[1,2,1]]) < view_distance([[-30,0,-30],[30,60,30]])


def test_one_building_edit_reuses_other_models_and_preserves_terrain(tmp_path, monkeypatch):
    monkeypatch.setenv('TURKU3D_WORK', str(tmp_path / 'work'))
    tile = C.VisualTile(0, -3, 2)
    shells, meshes = fixture(tile)
    work = tmp_path / 'products'; stage = work / tile.id / '.stage'
    stage.mkdir(parents=True)
    source = stage / 'architecture.vis.glb'
    write_visual(source, meshes, root_translation=tile.origin)
    layers, before, first = publish(tile, [source], shells, stage, work, tmp_path / 'no-library')
    terrain = layers[0].read_bytes()
    before_by_id = {p['id']: p['model'] for p in before['placements']}
    assert len(before_by_id) == 2
    assert before['bounds']['max'][0] > tile.bbox.xmax
    assert before['streamingBounds']['max'][0] > before['bounds']['max'][0]
    for model in before['models']:
        assert (work / tile.id / model['uri']).stat().st_size == model['bytes']
        js, _ = read_glb((work / tile.id / model['uri']).read_bytes())
        assert all(np.linalg.norm(n.get('translation', [0,0,0])) == 0 for n in js['nodes'])
        audit = json.loads(subprocess.check_output(['node',
            str(C.REPO_ROOT / 'pipeline/turku3d/package/visual_verify.mjs'),
            str(work / tile.id / model['uri']),
            str(work / '.visual-audits' / (model['id'] + '.snapshot'))], text=True,
            env={**os.environ, 'TURKU3D_GLTF_TOOLS': os.environ.get('TURKU3D_GLTF_TOOLS', str(Path.home() / 'turku3d/tools/npm'))}))
        assert audit['max_error_m'] < 0.001

    meshes[1].color = (.2,.4,.6,1)
    write_visual(source, meshes, root_translation=tile.origin)
    layers, after, second = publish(tile, [source], shells, stage, work, tmp_path / 'no-library')
    assert layers[0].read_bytes() == terrain
    after_by_id = {p['id']: p['model'] for p in after['placements']}
    assert before_by_id['inside'] == after_by_id['inside']
    assert before_by_id['crossing'] != after_by_id['crossing']
    assert second['inside']['package']['package']['cached'] is True
    assert second['crossing']['package']['package']['cached'] is False
    print(json.dumps({'first': first, 'one_building_edit': second}, sort_keys=True))


def test_shared_package_inputs_are_prepared_once_per_tile_with_unchanged_keys(tmp_path, monkeypatch):
    from turku3d.coarse import archetypes, packaging
    monkeypatch.setenv('TURKU3D_WORK', str(tmp_path / 'work'))
    tile = C.VisualTile(0, -3, 2)
    shells, meshes = fixture(tile)
    work = tmp_path / 'products'; stage = work / tile.id / '.stage'
    stage.mkdir(parents=True)
    source = stage / 'architecture.vis.glb'
    write_visual(source, meshes, root_translation=tile.origin)
    library = tmp_path / 'library'; library.mkdir()
    (library / 'plaster.json').write_text('{"roughness": 0.9}')
    calls = []
    original = packaging.shared_package_inputs
    counted = lambda *a, **k: calls.append(1) or original(*a, **k)
    monkeypatch.setattr(archetypes, 'shared_package_inputs', counted)
    monkeypatch.setattr(packaging, 'shared_package_inputs', counted)
    _, manifest, metrics = publish(tile, [source], shells, stage, work, library)
    assert len(manifest['models']) == 2 and len(calls) == 1
    # Packaging the same model without the precomputed inputs hits the same cache entry.
    identity = sorted(p.name for p in (stage / 'archetypes').iterdir() if len(p.name) == 64)[0]
    directory = stage / 'archetypes' / identity
    packaging.package_visual(C.VisualTile(0, 0, 0), [directory / 'model.vis.glb'], directory, work, library,
                             cache_unit=f'archetypes/{identity}')
    assert len(calls) == 2
    assert json.loads((directory / 'package-times.json').read_text())['package']['cached'] is True
