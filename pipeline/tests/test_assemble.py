import io
import json
import struct

import numpy as np
import pytest
from PIL import Image

from turku3d import config as C
from turku3d.layers import CollisionLayer, PRIMITIVE_DTYPE, write_collision, write_visual, write_manifest
from turku3d.package.assemble import assemble
from turku3d.package.gltf import GlbMesh, read_glb
from turku3d.package.manifest import verify_tree
from turku3d.package.seams import verify_seams
from turku3d.col.format import read_col


def fixture(root, ids=('t0_0_0',), *, image=False):
    ground = root / 'ground'
    files = []
    for tid in ids:
        tile = C.VisualTile.parse(tid)
        for c in tile.collision_tiles():
            x, _, z = c.origin; s = C.COLLISION_TILE_SIZE
            pos = np.array([[x, 2.12345678, z], [x+s, 2.12345678, z], [x, 2.12345678, z+s], [x+s, 2.12345678, z+s]])
            idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
            name = c.id + '.col.npz'
            write_collision(ground / name, CollisionLayer(pos, idx, np.array([1, 2], np.uint8)))
            files.append(name)
        x, _, z = tile.origin; s = tile.size
        pos = np.array([[x, 2.12345678, z], [x+s, 2.12345678, z], [x, 2.12345678, z+s], [x+s, 2.12345678, z+s]])
        extras = dict(layer='ground', object_id='ground_'+tid, material_key='road', texture_uv='normalized')
        mesh = GlbMesh(tid, pos-np.array(tile.origin), idx, uvs=np.array([[0,0],[1,0],[0,1],[1,1]], np.float32), extras=extras)
        if image:
            stream = io.BytesIO(); Image.new('RGB', (8,8), (128,150,80)).save(stream, format='PNG'); mesh.image = stream.getvalue()
        name = tid + '.vis.glb'; files.append(name)
        write_visual(ground / name, [mesh], root_translation=tile.origin)
    write_manifest(ground, inputs={'synthetic':'v1'}, params={}, code='test', tiles=files)


def test_assemble_roundtrip_textures_parents_cache_and_invalidation(tmp_path):
    layers, out = tmp_path/'layers', tmp_path/'dist'
    fixture(layers, image=True)
    library = tmp_path/'library'; (library/'road').mkdir(parents=True)
    Image.new('RGB',(8,8),(128,128,255)).save(library/'road/normal.png')
    Image.new('RGB',(8,8),(255,180,0)).save(library/'road/orm.png')
    Image.new('RGB',(8,8),(100,130,90)).save(library/'road/albedo.png')
    buildings = layers/'buildings'
    roof = GlbMesh('roof', np.array([[-10.,10,0],[10.,10,0],[-10.,10,10]]),
                   np.array([[0,2,1]],np.uint32), uvs=np.zeros((3,2),np.float32),
                   extras=dict(layer='buildings',object_id='roof',material_key='road',surface='roof'))
    wall = GlbMesh('facade', np.array([[-10.,0,0],[10.,0,0],[-10.,10,0]]),
                   np.array([[0,1,2]],np.uint32), uvs=np.zeros((3,2),np.float32),
                   extras=dict(layer='buildings',object_id='facade',material_key='road',building_id='b1',facade_index=0))
    write_visual(buildings/'t0_0_0.vis.glb',[roof,wall])
    write_manifest(buildings,inputs={},params={},code='test',tiles=['t0_0_0.vis.glb'])
    # Optional layer contributes triangles and a whole primitive crossing a tile edge.
    p = np.zeros(1, PRIMITIVE_DTYPE); p['kind']='box'; p['center']=[62.5,3,20]
    p['half_extents']=[1,1,1]; p['material']=19; p['yaw']=0.4
    structure = layers/'structures'
    write_collision(structure/'c_0_0.col.npz', CollisionLayer(
        np.array([[20.,2.12345678,20],[21.,2.12345678,20],[20.,2.12345678,21]]),
        np.array([[0,2,1]],np.uint32), np.array([19],np.uint8), p))
    write_manifest(structure, inputs={}, params={}, code='test', tiles=['c_0_0.col.npz'])
    result = assemble(['t0_0_0'], layers=layers, out=out, library=library)
    assert set(result['changed']) == {'t0_0_0','t1_0_0','t2_0_0'}
    assert verify_tree(out) == verify_seams(out) == []
    m = json.loads((out/'manifest.json').read_text())
    assert len(m['collision']) == 16
    for cid, entry in m['collision'].items():
        tile = read_col(out/entry['url'])
        assert max(abs(tile.world_positions()[:,1] - 2.12345678)) < .002
        assert sorted(tile.materials.tolist()) == ([1,2,19] if cid == 'c_0_0' else [1,2])
        assert entry['bytes'] <= 1_000_000
        if cid == 'c_0_0':
            assert len(tile.primitives) == 1
            assert np.allclose(tile.primitives[0].center, [62.5,3,20])
    glb, blob = read_glb((out/m['visual']['t0_0_0']['url']).read_bytes())
    assert 'EXT_meshopt_compression' in glb['extensionsRequired']
    assert 'KHR_texture_basisu' in glb['extensionsRequired']
    assert len(glb['images']) == 3  # Missing unique facade uses the library fallback.
    assert m['visual']['t1_0_0']['tris'] == 3  # Ground plus roof, no facade wall.
    assert m['visual']['t2_0_0']['tris'] == 3
    for img in glb['images']:
        assert img['mimeType'] == 'image/ktx2'
    mat = glb['materials'][0]
    def compression(texture_info):
        tex = glb['textures'][texture_info['index']]
        image = glb['images'][tex['extensions']['KHR_texture_basisu']['source']]
        bv = glb['bufferViews'][image['bufferView']]
        return struct.unpack_from('<I',blob,bv.get('byteOffset',0)+44)[0]
    assert compression(mat['pbrMetallicRoughness']['baseColorTexture']) == 1  # ETC1S/BasisLZ
    assert compression(mat['normalTexture']) == 2  # UASTC/Zstandard
    assert m['visual']['t0_0_0']['brotli_bytes'] <= 8_000_000
    assert m['visual']['t0_0_0']['max_error_m'] == 0
    ts = json.loads((out/'tileset.json').read_text())
    box = ts['root']['boundingVolume']['box']
    assert box[0] - box[3] <= -10  # Whole-object bounds extend outside the nominal tile.
    assert ts['root']['children'][0]['extras']['id'] == 't2_0_0'
    assert ts['root']['children'][0]['children'][0]['children'][0]['extras']['id'] == 't0_0_0'
    before = {p:p.stat().st_mtime_ns for p in out.rglob('*') if p.is_file()}
    assert assemble(['t0_0_0'], layers=layers, out=out, library=library)['changed'] == []
    assert before == {p:p.stat().st_mtime_ns for p in out.rglob('*') if p.is_file()}
    # Corrupt output invalidates a cache hit; repair must pass hashes again.
    (out/m['visual']['t0_0_0']['url']).write_bytes(b'broken')
    assert assemble(['t0_0_0'], layers=layers, out=out, library=library)['changed']
    assert verify_tree(out) == []
    # A newly supplied facade invalidates the content cache, then gets bound to this building only.
    img = glb['images'][0]; view = glb['bufferViews'][img['bufferView']]
    facades = tmp_path/'facades'; (facades/'b1').mkdir(parents=True)
    (facades/'b1/0.ktx2').write_bytes(blob[view.get('byteOffset',0):view.get('byteOffset',0)+view['byteLength']])
    assert assemble(['t0_0_0'], layers=layers, out=out, library=library, facades=facades)['changed']
    js, _ = read_glb((out/'visual/0/t0_0_0.glb').read_bytes())
    assert any(im.get('name') == '0.ktx2' for im in js['images'])


def test_seam_rejected_before_publishing(tmp_path):
    layers = tmp_path/'layers'; fixture(layers)
    from turku3d.layers import read_collision, read_manifest
    file = layers/'ground/c_1_0.col.npz'; layer = read_collision(file)
    layer.positions[0,1] += .01
    write_collision(file,layer)
    m = read_manifest(layers/'ground', verify=False)
    write_manifest(layers/'ground',inputs={},params={},code='test',tiles=list(m['tiles']))
    with pytest.raises(ValueError, match='seam mismatch'):
        assemble(['t0_0_0'], layers=layers, out=tmp_path/'out')
    assert not (tmp_path/'out/manifest.json').exists()


def test_missing_layers_have_actionable_error(tmp_path):
    with pytest.raises(ValueError, match='no published visual layers'):
        assemble(['t0_0_0'], layers=tmp_path/'missing', out=tmp_path/'out')


def test_incremental_neighbours_and_removed_collision_are_updated(tmp_path):
    from turku3d.layers import read_manifest
    from turku3d.col.format import FLAG_AOI_BORDER
    layers, out = tmp_path/'layers', tmp_path/'dist'
    fixture(layers, ids=('t0_0_0','t0_1_0'))
    assemble(['t0_0_0'],layers=layers,out=out)
    manifest = json.loads((out/'manifest.json').read_text())
    assert manifest['collision']['c_3_1']['flags'] & FLAG_AOI_BORDER
    assemble(['t0_1_0'],layers=layers,out=out)
    manifest = json.loads((out/'manifest.json').read_text())
    assert not manifest['collision']['c_3_1']['flags'] & FLAG_AOI_BORDER
    assert len(manifest['collision']) == 32
    m = read_manifest(layers/'ground')
    del m['tiles']['c_4_1.col.npz']
    write_manifest(layers/'ground',inputs={},params={},code='test',tiles=list(m['tiles']))
    assemble(['t0_1_0'],layers=layers,out=out)
    manifest = json.loads((out/'manifest.json').read_text())
    assert 'c_4_1' not in manifest['collision']
    assert not (out/'collision/t0_1_0/c_4_1.col').exists()
    assert manifest['collision']['c_3_1']['flags'] & FLAG_AOI_BORDER
    assert verify_tree(out) == verify_seams(out) == []


def test_structures_only_can_ship_without_empty_parent_content(tmp_path):
    directory = tmp_path/'layers/structures'
    mesh = GlbMesh('wall', np.array([[0.,0,0],[1,0,0],[0,1,0]]), np.array([[0,1,2]],np.uint32),
                   extras=dict(layer='structures',object_id='wall',material_key='concrete'))
    write_visual(directory/'t0_0_0.vis.glb',[mesh])
    write_manifest(directory,inputs={},params={},code='test',tiles=['t0_0_0.vis.glb'])
    out = tmp_path/'out'
    assemble(['t0_0_0'],layers=directory.parent,out=out)
    m = json.loads((out/'manifest.json').read_text())
    assert set(m['visual']) == {'t0_0_0'}
    assert m['collision'] == {}
    assert assemble(['t0_0_0'],layers=directory.parent,out=out)['changed'] == []
