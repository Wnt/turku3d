"""Pinned source and texture closure must survive every bounded rebuild."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from turku3d.scene.compile import release_hash, resolve_spawn
from turku3d.scene.recipe import VERSION, copy_assets, validate_recipe


class SceneRecipeTests(unittest.TestCase):
    def recipe(self):
        return dict(version=VERSION,id='pilot',bounds=[0.,0.,10.,10.],inputs=[],assets=[],materialRecipes=[],
                    trees=[],assetSources=[],fixtureBindings=None,
                    spawn=dict(position=[1.,1.,1.],headingDeg=0.,vehicle=dict(position=[2.,1.,2.],yaw=0.)))

    def test_required_inputs_and_safe_relative_paths(self):
        recipe=self.recipe();validate_recipe(recipe)
        for mutate in (lambda r:r.pop('trees'),lambda r:r.pop('spawn'),
                       lambda r:r['spawn'].update(position=[10.,1.,1.]),lambda r:r.update(version='old-scene'),
                       lambda r:r['inputs'].append(dict(path='../raw.pkl',sha256='0'*64)),
                       lambda r:r.update(bounds=[0.,0.,0.,10.])):
            broken=copy.deepcopy(recipe);mutate(broken)
            with self.assertRaises(ValueError):validate_recipe(broken)

    def test_all_external_glb_maps_are_required_and_hash_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source';out=Path(directory)/'output';root.mkdir()
            texture=b'generated-map-bytes';sha=hashlib.sha256(texture).hexdigest();name=sha+'.ktx2'
            buffer=b'externally-addressed-buffer';buffer_sha=hashlib.sha256(buffer).hexdigest();buffer_name=buffer_sha+'.bin'
            document=json.dumps(dict(asset=dict(version='2.0'),images=[dict(uri=name)],buffers=[dict(uri=buffer_name,byteLength=len(buffer))])).encode()
            document+=b' '*((-len(document))%4)
            glb=struct.pack('<III',0x46546c67,2,20+len(document))+struct.pack('<II',len(document),0x4e4f534a)+document
            (root/'source.glb').write_bytes(glb)
            recipe=self.recipe();recipe['assets']=[dict(url='source.glb',sha256=hashlib.sha256(glb).hexdigest())]
            with self.assertRaises(FileNotFoundError):copy_assets(recipe,root,out)
            (root/name).write_bytes(b'wrong content')
            with self.assertRaises(ValueError):copy_assets(recipe,root,out)
            (root/name).write_bytes(texture)
            with self.assertRaises(FileNotFoundError):copy_assets(recipe,root,out)
            (root/buffer_name).write_bytes(buffer)
            self.assertEqual(copy_assets(recipe,root,out),3)
            self.assertEqual((out/buffer_name).read_bytes(),buffer)
            self.assertEqual((out/name).read_bytes(),texture)

    def test_release_identity_includes_collision_and_asset_provenance(self):
        scene=dict(id='pilot',release='pending',collision={'c_0_0':dict(sha256='a'*64)},sources=[])
        original=release_hash(scene)
        scene['release']='not-an-input';self.assertEqual(release_hash(scene),original)
        scene['collision']['c_0_0']['sha256']='b'*64;self.assertNotEqual(release_hash(scene),original)
        scene['collision']['c_0_0']['sha256']='a'*64
        scene['sources'].append(dict(id='approved-building',sha256='c'*64))
        self.assertNotEqual(release_hash(scene),original)

    def test_authored_spawn_height_selects_the_supported_level(self):
        scene=dict(bounds=[0.,0.,10.,10.],spawn=self.recipe()['spawn'],meshes=[],supports=[])
        for name,height in [('lower',1.),('deck',6.)]:
            scene['meshes'].append(dict(id=name,positions=[0.,height,0.,0.,height,10.,10.,height,0.],indices=[0,1,2]))
            scene['supports'].append(dict(id=name,meshId=name))
        scene['spawn']['position']=[1.,5.8,1.]
        resolve_spawn(scene)
        self.assertEqual(scene['spawn']['position'],[1.,6.15,1.])
        self.assertEqual(scene['spawn']['vehicle']['position'],[2.,1.15,2.])
        scene['spawn']['position']=[9.,1.,9.]
        with self.assertRaisesRegex(ValueError,'lacks prepared support'):resolve_spawn(scene)


if __name__=='__main__':unittest.main()
