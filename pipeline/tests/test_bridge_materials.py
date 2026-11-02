"""Bridge PBR channels survive export without changing structural freshness."""
from io import BytesIO
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import numpy as np
from PIL import Image

from turku3d import config as C
from turku3d.coarse import bridge_materials as material


def test_stone_and_bronze_maps_preserve_physical_channels():
    stone, bronze = material.maps('granite'), material.maps('bronze')
    stone_orm = np.asarray(Image.open(BytesIO(stone['orm_image'])))
    bronze_orm = np.asarray(Image.open(BytesIO(bronze['orm_image'])))
    assert stone_orm[:, :, 2].max() == 0
    assert bronze_orm[:, :, 2].min() > 0
    assert stone_orm[:, :, 1].mean() > bronze_orm[:, :, 1].mean()
    normal = np.asarray(Image.open(BytesIO(stone['normal_image']))).astype(float) / 127.5 - 1
    assert np.max(abs(np.linalg.norm(normal, axis=-1) - 1)) < .015
    assert normal[:, :, 2].min() > .6
    assert material.maps('granite') == stone


def test_bridge_glb_exports_repeat_pbr(tmp_path):
    from turku3d.coarse.bridge_kit import tapered_post
    from turku3d.coarse.bridges import visual

    mesh = tapered_post('pylon', [10, 5, 10], [1, 0], [(0, 1, 1), (4, .5, .5)])
    layer_files = []
    visual([SimpleNamespace(meshes=[mesh])], C.VisualTile(0, 0, 0), tmp_path, layer_files)
    data = (tmp_path / 'bridges.vis.glb').read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    gltf = json.loads(data[20:20 + length])
    mat = gltf['materials'][0]
    assert 'normalTexture' in mat
    assert 'occlusionTexture' in mat
    pbr = mat['pbrMetallicRoughness']
    assert 'metallicRoughnessTexture' in pbr
    assert pbr['metallicFactor'] == pbr['roughnessFactor'] == 1
    assert len(gltf['images']) == 3
    assert all(s['wrapS'] == s['wrapT'] == 10497 for s in gltf['samplers'])
    assert layer_files


def test_material_only_changes_invalidate_visuals_but_not_collision():
    from turku3d.coarse import collision_key, packaging

    path = Path(material.__file__)
    assert path in packaging.producer_paths()
    assert path not in collision_key.paths()


def test_stairs_and_quay_vaults_use_the_same_pbr_export():
    from turku3d.coarse import stairs
    from turku3d.coarse.tunnels import Tunnels
    from turku3d.coarse.bridge_kit import tapered_post

    mesh = tapered_post('stairs:test', [10, 5, 10], [1, 0], [(0, 1, 1), (1, 1, 1)])
    tile = C.VisualTile(0, 0, 0)
    stair = stairs.visuals(tile, [mesh])[0]
    mesh.metadata['vault'] = True
    vault = Tunnels(np.array([], bool), [mesh], [], [], 1).visuals(tile)[0]
    for visual in (stair, vault):
        assert visual.image and visual.normal_image and visual.orm_image
        assert np.allclose(visual.uvs, mesh.uvs / material.METRES)


def test_normal_green_compensates_loader_derivative_tangents(monkeypatch):
    size = material.SIZE
    y, x = np.mgrid[:size, :size] * material.METRES / size
    height = .001 * np.sin(x * np.pi) + .002 * np.cos(y * np.pi)
    monkeypatch.setattr(material, 'fields', lambda _: (height, np.full((size, size, 3), 128, np.uint8),
                                                       np.zeros((size, size, 3), np.uint8)))
    material.maps.cache_clear()
    normal = np.asarray(Image.open(BytesIO(material.maps('probe')['normal_image']))).astype(float) / 127.5 - 1
    # Loader uses normalScale=(1,-1) for this export (no TANGENT accessor).
    normal[..., 1] *= -1
    dx = np.roll(height, -1, 1) - np.roll(height, 1, 1)
    dy = np.roll(height, -1, 0) - np.roll(height, 1, 0)
    assert np.sum(normal[..., 0] * dx) < 0
    assert np.sum(normal[..., 1] * dy) < 0
    material.maps.cache_clear()


def test_library_asphalt_keeps_metric_uvs_before_packager_transform(tmp_path):
    from turku3d.coarse.bridge_kit import tapered_post
    from turku3d.coarse.bridges import visual

    mesh = tapered_post('road', [10, 5, 10], [1, 0], [(0, 2, 2), (.2, 2, 2)], 'asphalt')
    visual([SimpleNamespace(meshes=[mesh])], C.VisualTile(0, 0, 0), tmp_path, [])
    data = (tmp_path / 'bridges.vis.glb').read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    gltf = json.loads(data[20:20 + length])
    prim = gltf['meshes'][0]['primitives'][0]
    accessor = gltf['accessors'][prim['attributes']['TEXCOORD_0']]
    view = gltf['bufferViews'][accessor['bufferView']]
    offset = 28 + length + view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
    uvs = np.frombuffer(data, dtype='<f4', count=accessor['count'] * 2, offset=offset).reshape(-1, 2)
    # Shared ground_asphalt adds the library's 0.5 repeat transform at packaging.
    assert np.allclose(uvs, mesh.uvs)
    assert any(n.get('extras', {}).get('material_key') == 'ground_asphalt' for n in gltf['nodes'])
