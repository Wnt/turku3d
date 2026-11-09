import copy
import hashlib
import json

import numpy as np
import pytest

from turku3d import config as C
from turku3d.migrations import centred_paint, core
from turku3d.migrations.furniture import read_accessor
from turku3d.package.gltf import GlbMesh, write_glb
from turku3d.roads.paint import graph_key


def plane(y):
    return np.array([[[0, y, 0], [0, y, 10], [10, y, 0]],
                     [[10, y, 0], [0, y, 10], [10, y, 10]]], dtype=float)


def test_real_drape_uses_visible_height_not_collision_height():
    import shapely
    from turku3d.col.materials import mat_id
    from turku3d.coarse.surfaces import GROUND_PAINT_LIFT

    visible, collision = plane(.3), plane(0)
    visible, materials = centred_paint.partition_visible(visible, collision, np.full(2, mat_id('road')))
    mark = (shapely.box(2, 2, 3, 5), (1, 1, 1), False, 'centre')
    mesh = centred_paint.drape_visible(C.VisualTile.parse('t0_0_0'), visible, materials, [mark])
    np.testing.assert_allclose(mesh.positions[:, 1], .3 + GROUND_PAINT_LIFT)


@pytest.mark.parametrize('deck,height', [(True, 5.412), (False, .308)])
def test_stacked_deck_and_road_only_receive_their_own_paint(deck, height):
    import shapely
    from turku3d.col.materials import mat_id

    visible = np.concatenate([plane(.3), plane(5.4)])
    collision = np.concatenate([plane(0), plane(5)])
    kinds = np.array([mat_id('road')] * 2 + [mat_id('bridge_deck')] * 2)
    visible, materials = centred_paint.partition_visible(visible, collision, kinds)
    assert set(materials) == set(kinds)
    mark = (shapely.box(2, 2, 3, 5), (1, 1, 1), deck, 'centre')
    mesh = centred_paint.drape_visible(C.VisualTile.parse('t0_0_0'), visible, materials, [mark])
    np.testing.assert_allclose(mesh.positions[:, 1], height)
    triangles = mesh.positions[mesh.indices]
    area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1).sum() / 2
    assert area == pytest.approx(3)


def product(tmp_path, tile='t0_0_0'):
    directory = tmp_path / tile
    directory.mkdir()
    graph = dict(nodes=[0, 0, 10, 1], edges=[[0, 1, 19, 10]], edgeAttrs=[], edgeEvents=[],
                 seamOrigins={'1': [10, 0]}, edgeHeights=[[2, 2]])
    roads = json.dumps(graph).encode()
    (directory / 'roads.json').write_bytes(roads)
    positions = np.array([[0, 2, 0], [0, 2, 1], [1, 2, 0]], dtype=float)
    mesh = GlbMesh('paint', positions, np.array([[0, 1, 2]]),
                   colors=np.ones((3, 3)), baked_visibility=np.full((3, 3), .6),
                   extras=dict(object_id=f'paint_{tile}', layer='markings'))
    other = GlbMesh('untouched', positions + 20, np.array([[0, 1, 2]]))
    data = write_glb([mesh, other], directory / 'temp.glb', root_translation=C.VisualTile.parse(tile).origin)
    name = f'vis.{hashlib.sha256(data).hexdigest()}.glb'
    (directory / name).write_bytes(data)
    manifest = dict(tile=tile, roads=dict(url='roads.json'), collision={'unchanged': {'url': 'col.bin'}},
                    visual={tile: dict(url=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), tris=2)},
                    vehicles={'parked': ['unchanged']})
    return core.Product(directory, manifest), graph, mesh


@pytest.mark.parametrize('tile', ['t0_0_0', 't0_0_1'])
def test_paint_refresh_preserves_other_geometry_and_is_idempotent(tmp_path, monkeypatch, tile):
    p, graph, mesh = product(tmp_path, tile)
    original = copy.deepcopy(p.manifest)
    old_doc, old_binary = core.glb_chunks(p.read(original['visual'][p.tile]['url']))
    moved = copy.deepcopy(mesh)
    moved.positions[:, 2] += 1.15
    monkeypatch.setattr(centred_paint, 'refreshed_mesh', lambda *_: (moved, {'centre': 1}))
    centred_paint.transform(p)
    entry = p.manifest['visual'][p.tile]
    doc, binary = core.glb_chunks(p.read(entry['url']))
    assert binary == old_binary
    assert doc['nodes'] == old_doc['nodes']
    assert doc['materials'] == old_doc['materials']
    assert doc['meshes'][1:] == old_doc['meshes'][1:]
    for key in ('roads', 'collision', 'vehicles'):
        assert p.manifest[key] == original[key]
    primitive = doc['meshes'][0]['primitives'][0]
    position = read_accessor(doc, binary, p, entry['url'], primitive['attributes']['POSITION'])
    np.testing.assert_allclose(position, moved.positions)
    visibility = read_accessor(doc, binary, p, entry['url'], primitive['attributes']['COLOR_1'])
    np.testing.assert_allclose(visibility[:, :3], .6, atol=.005)
    np.testing.assert_allclose(visibility[:, 3], 1)
    assert primitive['material'] == old_doc['meshes'][0]['primitives'][0]['material']
    assert p.manifest['paint_road_graph'] == graph_key(graph)
    before, writes = copy.deepcopy(p.manifest), dict(p.written)
    centred_paint.transform(p)
    assert p.manifest == before and p.written == writes


def test_full_build_receipt_and_unmoved_tiles_do_not_redrape(tmp_path, monkeypatch):
    p, graph, _ = product(tmp_path)
    def unexpected(*_):
        raise AssertionError('a fresh full build must retain its visual-floor drape')
    monkeypatch.setattr(centred_paint, 'refreshed_mesh', unexpected)
    p.manifest['paint_road_graph'] = graph_key(graph)
    centred_paint.transform(p)
    assert not p.written
    del p.manifest['paint_road_graph']
    graph.pop('seamOrigins')
    (p.directory / 'roads.json').write_text(json.dumps(graph))
    centred_paint.transform(p)
    assert not p.written


def test_paint_receipt_tracks_planar_events_but_not_height_repairs():
    graph = dict(nodes=[0, 0, 10, 1], edges=[[0, 1, 19, 10]], edgeEvents=[[4, 2, -1]], edgeHeights=[[2, 2]])
    old = graph_key(graph)
    graph['edgeHeights'] = [[3, 4]]
    assert graph_key(graph) == old
    graph['nodes'][3] = 2
    assert graph_key(graph) != old
    old = graph_key(graph)
    graph['edgeEvents'][0][0] = 5
    assert graph_key(graph) != old


def test_semantic_boundary_clips_visible_triangle_instead_of_its_centroid():
    import shapely
    from turku3d.col.materials import mat_id

    road, sidewalk = plane(0), plane(0)
    road[:, :, 0] *= .6
    sidewalk[:, :, 0] = 6 + sidewalk[:, :, 0] * .4
    collision = np.concatenate([road, sidewalk])
    visible, kinds = centred_paint.partition_visible(plane(.3), collision,
        np.array([mat_id('road')] * 2 + [mat_id('sidewalk')] * 2))
    mark = (shapely.box(7, 1, 8, 2), (1, 1, 1), False, 'centre')
    assert centred_paint.drape_visible(C.VisualTile.parse('t0_0_0'), visible, kinds, [mark]) is None
    mark = (shapely.box(5, 1, 7, 2), (1, 1, 1), False, 'centre')
    mesh = centred_paint.drape_visible(C.VisualTile.parse('t0_0_0'), visible, kinds, [mark])
    assert mesh.positions[:, 0].max() == pytest.approx(6)
    np.testing.assert_allclose(mesh.positions[:, 1], .308)


@pytest.mark.parametrize('object_id,key', [('draft-bridge:123:deck', 'ground_asphalt'),
    ('draft-bridge:124:deck', 'draft_bridge_stone'), ('bridge:123:road', 'bridge_asphalt')])
def test_visible_floor_reads_production_bridge_tops_but_not_rails(tmp_path, object_id, key):
    from turku3d.col import ColTile, write_col
    from turku3d.col.materials import mat_id

    p, _, _ = product(tmp_path)
    meshes = []
    for oid, y, material in [(object_id, 5.4, key), ('draft-bridge:123:rail', 6.5, 'draft_bridge_steel')]:
        meshes.append(GlbMesh(oid, plane(y).reshape(-1, 3), np.arange(6).reshape(-1, 3),
                             extras=dict(layer='bridges', object_id=oid, material_key=material)))
    data = write_glb(meshes, tmp_path / 'bridges.glb')
    p.written['bridge.glb'] = data
    p.manifest['visual'][p.tile]['url'] = 'bridge.glb'
    positions = plane(5).reshape(-1, 3)
    col = ColTile.for_tile(0, 0, positions, np.arange(6, dtype=np.uint32).reshape(-1, 3),
                          np.full(2, mat_id('bridge_deck'), dtype=np.uint8))
    p.written['col.bin'] = write_col(col, encoding='raw', optimize=False)
    visible, kinds = centred_paint.visible_floor(p)
    assert len(visible) == 2
    np.testing.assert_allclose(visible[:, :, 1], 5.4, atol=1e-6)
    assert set(kinds) == {mat_id('bridge_deck')}


def test_bridge_rule_keeps_at_grade_approach_beyond_deck():
    import shapely
    from turku3d.col.materials import mat_id

    road, deck = plane(.3), plane(5.4)
    deck[:, :, 0] *= .5
    visible = np.concatenate([road, deck])
    kinds = np.array([mat_id('road')] * 2 + [mat_id('bridge_deck')] * 2)
    mark = (shapely.box(4, 1, 6, 2), (1, 1, 1), True, 'centre')
    mesh = centred_paint.drape_visible(C.VisualTile.parse('t0_0_0'), visible, kinds, [mark])
    triangles = mesh.positions[mesh.indices]
    for height in (.308, 5.412):
        level = triangles[np.isclose(triangles[:, :, 1].mean(axis=1), height)]
        area = np.linalg.norm(np.cross(level[:, 1] - level[:, 0], level[:, 2] - level[:, 0]), axis=1).sum() / 2
        assert area == pytest.approx(1)
