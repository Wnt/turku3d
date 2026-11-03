"""Nearby whole buildings form blocks whose LOD parent is their LoD2 shells."""
import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import blocks
from turku3d.coarse.inputs import Building
from turku3d.package.gltf import GlbMesh, read_glb


def box_shell(x0, z0, x1, z1, top):
    roof = np.array([[x0, top, z0], [x1, top, z0], [x1, top, z1], [x0, top, z1]], float)
    walls = np.array([[x0, 0, z0], [x1, 0, z0], [x1, top, z0], [x0, top, z0]], float)
    quad = np.array([[0, 1, 2], [0, 2, 3]])
    return (roof, quad, roof.copy(), quad[:, ::-1], walls, np.zeros((4, 2)), quad)


def model(key, x0, z0, x1, z1, top=12, glazed=True):
    building = Building(key, shapely.box(x0, z0, x1, z1), 0, top, 'plaster')
    kit = dict(wall="#b8b394", glazing_scope="window" if glazed else None)
    mesh = GlbMesh(key, np.zeros((3, 3)), np.array([[0, 1, 2]]), extras=dict(layer='buildings', facade_kit=kit))
    return (key, np.array([(x0 + x1) / 2, 0, (z0 + z1) / 2]), [mesh], [(building, box_shell(x0, z0, x1, z1, top))])


def test_nearby_buildings_share_a_block_and_wide_blocks_split():
    tile = C.VisualTile(0, 0, 0)
    models = [model('a', 0, 0, 20, 20), model('b', 30, 0, 50, 20), model('c', 120, 0, 140, 20)]
    ids = blocks.assign(tile, models)
    assert ids[0] == ids[1] != ids[2]
    row = [model(f'r{i}', i * 22, 100, i * 22 + 20, 120) for i in range(8)]  # 174 m terrace
    ids = blocks.assign(tile, row)
    assert len(set(ids)) >= 2


def test_lod_parent_is_shell_roofs_and_walls_in_the_block_frame_with_glazing_marked():
    a, b = model('a', 0, 0, 20, 20), model('b', 30, 0, 50, 20, glazed=False)
    meshes = blocks.lod_meshes('bX', [(a[2], a[3]), (b[2], b[3])], np.array([25.0, 0, 10.0]))
    names = sorted(m.extras['lod'] for m in meshes)
    assert names == ['roofs', 'walls', 'walls-glazed']
    assert sum(len(m.indices) for m in meshes) == 8
    roof = next(m for m in meshes if m.extras['lod'] == 'roofs')
    assert roof.positions[:, 0].min() == -25 and roof.positions[:, 0].max() == 25
    glazed = next(m for m in meshes if m.extras['lod'] == 'walls-glazed')
    np.testing.assert_allclose(glazed.colors[0], blocks._linear('#b8b394', None))
    from turku3d.package.gltf import build_glb
    document, _ = read_glb(build_glb(meshes, root_translation=(0., 0., 0.)))
    assert len(document['meshes']) == 3
