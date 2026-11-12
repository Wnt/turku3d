"""Source assets preserve world placement and external-buffer identity."""
import hashlib
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
import numpy as np
import shapely
from turku3d.scene.asset_buffers import embedded_buffers,encode_glb,node_world_matrices
from turku3d.scene.assets import import_vegetation
from turku3d.package.gltf import read_glb


class SceneAssetTests(unittest.TestCase):
    def test_library_surface_response_preserves_pbr_and_metric_texture_transform(self):
        from turku3d.scene.assets import import_materials
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); raw=b'authored-texture-bytes'
            info=dict(index=0,extensions={'KHR_texture_transform':{'scale':[.5,.5]}})
            materials=[dict(extras=dict(library_material_key=key),pbrMetallicRoughness=dict(baseColorFactor=[.7,.8,.9,1],roughnessFactor=.87,metallicFactor=.1,baseColorTexture=info,metallicRoughnessTexture=info),normalTexture={**info,"scale":.35})
                       for key in ('draft_asphalt_worn','draft_concrete_slabs','draft_quay_stone','draft_cobblestone')]
            document=dict(asset=dict(version='2.0'),materials=materials,textures=[dict(source=0)],images=[dict(bufferView=0,mimeType='image/ktx2')],buffers=[dict(byteLength=len(raw))],bufferViews=[dict(buffer=0,byteLength=len(raw))])
            source=root/'source.glb';source.write_bytes(encode_glb(document,raw))
            rows=import_materials(source,source.as_uri(),root/'output')
            expected={'road':'asphalt','footway':'stone','quay':'stone','quay-wall':'stone','ground':'cobblestone','cobblestone':'cobblestone'}
            for row in rows:
                material=row['material'];self.assertEqual(material['surface'],expected[material['id']])
                self.assertEqual(material['color'],[.7,.8,.9]);self.assertEqual(material['roughness'],.87);self.assertEqual(material['metalness'],.1)
                self.assertEqual(row['uvScale'],[.5,.5]);self.assertEqual(material['normalScale'],.35)
                for key in ('baseColorTexture','normalTexture','ormTexture'):
                    self.assertEqual((root/'output'/material[key]['url']).read_bytes(),raw)
                    self.assertEqual(material[key]['sha256'],hashlib.sha256(raw).hexdigest())

    def test_external_buffers_relocate_compressed_views_and_keep_virtual_fallback(self):
        raw=b'abcdefgh';sha=hashlib.sha256(raw).hexdigest()
        doc=dict(buffers=[dict(uri=sha+'.bin',byteLength=8),dict(byteLength=100,extensions={'EXT_meshopt_compression':{'fallback':True}})],bufferViews=[dict(buffer=1,byteLength=20,extensions={'EXT_meshopt_compression':dict(buffer=0,byteOffset=4,byteLength=4)})])
        result,binary=embedded_buffers(doc,b'','https://source/',lambda _:raw)
        self.assertEqual(binary,raw);self.assertEqual(result['bufferViews'][0]['buffer'],1)
        self.assertEqual(result['bufferViews'][0]['extensions']['EXT_meshopt_compression']['byteOffset'],4)
        with self.assertRaises(ValueError):embedded_buffers(doc,b'','https://source/',lambda _:b'wrong')

    def test_nonzero_parent_translation_is_preserved_once_and_ground_is_world_space(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);raw=np.asarray([[0,0,0],[1,0,0],[0,1,0],[2,1,3]],dtype='<f4').tobytes();sha=hashlib.sha256(raw).hexdigest();(root/(sha+'.bin')).write_bytes(raw)
            doc=dict(asset=dict(version='2.0'),scene=0,scenes=[dict(nodes=[0])],nodes=[dict(translation=[250,0,250],children=[1]),dict(mesh=0,extras=dict(layer='vegetation',instance_object_ids=['tree:source'],instance_species=['birch'],instance_crowns=[1]),extensions={'EXT_mesh_gpu_instancing':{'attributes':{'TRANSLATION':1}}})],meshes=[dict(primitives=[dict(attributes={'POSITION':0},material=0)])],materials=[dict(pbrMetallicRoughness={})],textures=[],images=[],buffers=[dict(uri=sha+'.bin',byteLength=len(raw))],bufferViews=[dict(buffer=0,byteLength=36),dict(buffer=0,byteOffset=36,byteLength=12)],accessors=[dict(bufferView=0,componentType=5126,count=3,type='VEC3',min=[0,0,0],max=[1,1,0]),dict(bufferView=1,componentType=5126,count=1,type='VEC3')])
            source=root/'source.glb';source.write_bytes(encode_glb(doc,b''))
            scene=dict(supports=[dict(id='ground',meshId='ground')],meshes=[dict(id='ground',positions=[250,4,250,260,4,250,250,4,260],indices=[0,1,2])])
            asset,trees=import_vegetation(source,source.as_uri(),root/'output',shapely.Polygon(),scene,asset_id='vegetation:t0_1_1')
            self.assertEqual(asset['id'],'vegetation:t0_1_1');self.assertEqual(trees[0]['position'],[252.,4.,253.])
            prepared,binary=read_glb((root/'output'/asset['url']).read_bytes());node=prepared['nodes'][0]
            self.assertEqual(node['matrix'][12:15],[250.,0.,250.]);self.assertEqual(node['extras']['instance_species'],['birch'])
            index=node['extensions']['EXT_mesh_gpu_instancing']['attributes']['TRANSLATION'];accessor=prepared['accessors'][index];view=prepared['bufferViews'][accessor['bufferView']]
            self.assertEqual(struct.unpack_from('<fff',binary,view['byteOffset']),(2.,4.,3.))
            self.assertLess(asset['bounds']['min'][0],252);self.assertGreater(asset['bounds']['max'][2],253)
            again,again_trees=import_vegetation(root/'output'/asset['url'],(root/'output'/asset['url']).as_uri(),root/'second',shapely.Polygon(),scene,asset_id=asset['id'])
            self.assertEqual(again,asset);self.assertEqual(again_trees,trees)

            # Two source placement groups of one species must not collapse into
            # a single runtime parent/variant batch after support refresh.
            second=copy.deepcopy(doc['nodes'][1]);second['extras']['instance_object_ids']=['tree:second']
            doc['nodes'].extend([dict(translation=[255,0,250],children=[3]),second])
            doc['scenes'][0]['nodes'].append(2);source.write_bytes(encode_glb(doc,b''))
            grouped,group_trees=import_vegetation(source,source.as_uri(),root/'grouped',shapely.Polygon(),scene,asset_id='grouped')
            prepared,_=read_glb((root/'grouped'/grouped['url']).read_bytes())
            roots=prepared['scenes'][0]['nodes']
            self.assertEqual(len(roots),2)
            self.assertEqual([prepared['nodes'][i]['children'] for i in roots],[[0],[1]])
            self.assertEqual({t['id'] for t in group_trees},{'tree:source','tree:second'})
            again,_=import_vegetation(root/'grouped'/grouped['url'],(root/'grouped'/grouped['url']).as_uri(),root/'grouped-again',shapely.Polygon(),scene,asset_id='grouped')
            self.assertEqual(again,grouped)

    def test_complete_approved_building_owns_context_beyond_playable_edge(self):
        from turku3d.scene.context import outside_mesh
        row=dict(name='buildings',layer='buildings',positions=[[11,5,0],[13,5,0],[11,5,2],[20,5,0],[22,5,0],[20,5,2]],indices=[0,1,2,3,4,5],colors=[[1,1,1]]*6)
        mesh=outside_mesh(row,[0,0,10,10],[shapely.box(10,-1,14,3)])
        self.assertEqual(len(mesh.indices),1);self.assertGreaterEqual(mesh.positions[:,0].min(),20)
        row['layer']='ground'
        self.assertEqual(len(outside_mesh(row,[0,0,10,10],[shapely.box(10,-1,14,3)]).indices),2)

    def test_parent_rotation_and_scale_compose_before_child_translation(self):
        doc=dict(nodes=[dict(translation=[250,0,0],rotation=[0,0,2**-.5,2**-.5],scale=[2,2,2],children=[1]),dict(translation=[1,0,0])])
        np.testing.assert_allclose(node_world_matrices(doc)[1][:3,3],[250,2,0],atol=1e-12)
