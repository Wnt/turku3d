import numpy as np
import pytest
from turku3d.layers import (CollisionLayer, PRIMITIVE_DTYPE, read_collision, write_collision,
                            read_visual, write_visual, read_manifest, write_manifest)
from turku3d.package.gltf import GlbMesh


def test_layer_roundtrip_and_manifest(tmp_path):
    d = tmp_path / 'ground'
    a = CollisionLayer(np.array([[0., 0, 0], [1, 0, 0], [0, 0, 1]]),
                       np.array([[0, 2, 1]], np.uint32), np.array([1], np.uint8))
    p = np.zeros(1, PRIMITIVE_DTYPE)
    p['kind'] = 'box'; p['material'] = 1; p['half_extents'] = 1
    a.primitives = p
    write_collision(d / 'c_0_0.col.npz', a)
    b = read_collision(d / 'c_0_0.col.npz')
    assert a.positions.tobytes() == b.positions.tobytes()
    assert a.primitives.tobytes() == b.primitives.tobytes()
    mesh = GlbMesh('ground', a.positions, a.indices,
                   extras=dict(layer='ground', object_id='ground', material_key='asphalt'))
    write_visual(d / 't0_0_0.vis.glb', [mesh])
    assert read_visual(d / 't0_0_0.vis.glb')[:4] == b'glTF'
    m = write_manifest(d, inputs={'synthetic': 'test'}, params={}, code='test',
                       tiles=['c_0_0.col.npz', 't0_0_0.vis.glb'])
    assert read_manifest(d) == m
    (d / 'c_0_0.col.npz').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash/size'):
        read_manifest(d)


def test_reject_invalid_arrays_and_visual_extras(tmp_path):
    a = CollisionLayer(np.zeros((3, 3), np.float32), np.array([[0, 1, 2]], np.uint32), np.array([1], np.uint8))
    with pytest.raises(ValueError, match='positions'):
        write_collision(tmp_path / 'x', a)
    a.positions = a.positions.astype(np.float64); a.material[0] = 0
    with pytest.raises(ValueError, match='unshippable'):
        a.validate()
    with pytest.raises(ValueError, match='extras'):
        write_visual(tmp_path / 'x', [GlbMesh('x', a.positions, a.indices)])
