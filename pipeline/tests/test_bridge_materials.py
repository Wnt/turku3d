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
