"""Appearance-only publication reuses geometry, lighting and collision products."""
import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from turku3d.scene.appearance import SCHEMA, apply_patch, check_appearance_only, publish
from turku3d.scene.compile import release_hash
from turku3d.scene.recipe import VERSION
from turku3d.scene.regions import SCHEMA as REGIONS


def sha(data):
    return hashlib.sha256(data).hexdigest()


def glb(tag):
    document = json.dumps(dict(asset=dict(version='2.0', extras=dict(tag=tag)))).encode()
    document += b' ' * ((-len(document)) % 4)
    return struct.pack('<III', 0x46546c67, 2, 20+len(document)) + struct.pack('<II', len(document), 0x4e4f534a) + document


class SceneAppearanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store, self.scenes = root/'store', root/'scenes'
        (self.store/'shared').mkdir(parents=True); self.scenes.mkdir()
        self.old_glb = glb('old'); self.old_sha = sha(self.old_glb)
        (self.store/'shared'/(self.old_sha+'.glb')).write_bytes(self.old_glb)
        spawn = dict(position=[1., 1., 1.], headingDeg=0., vehicle=dict(position=[2., 1., 2.], yaw=0.))
        road = dict(id='road', color=[1, 1, 1], roughness=1, metalness=0)
        wall = dict(id='wall', color=[.5, .4, .3], roughness=.9, metalness=0)
        asset = dict(id='B', entityId='B', url=f'shared/{self.old_sha}.glb', sha256=self.old_sha, position=[0., 0., 0.],
                     recipe='building', bounds=dict(min=[0., 0., 0.], max=[1., 1., 1.]))
        frontage = dict(id='building:B:frontage', sha256=self.old_sha, kind='registered-frontage-appearance', uncertaintyM=.15)
        recipe = dict(version=VERSION, id='pilot', bounds=[0., 0., 10., 10.], spawn=spawn, inputs=[], assets=[copy.deepcopy(asset)],
                      materialRecipes=[dict(material=copy.deepcopy(road), uvScale=[.5, .5])], trees=[],
                      assetSources=[copy.deepcopy(frontage)], fixtureBindings=None)
        recipe_raw = (json.dumps(recipe, indent=2)+'\n').encode()
        self.recipe_path = self.scenes/'pilot.recipe.json'; self.recipe_path.write_bytes(recipe_raw)
        cell = b'collision-bytes'; self.cell = cell
        mesh = lambda key, material: dict(id=key, entityId=key, materialId=material, positions=[0., 0., 0., 1., 0., 0., 0., 0., 1.],
                                          indices=[0, 1, 2], collision='solid', supportId=None, uv=[0., 0., .5, 0., 0., .5])
        scene = dict(schema='turku-scene/1', release='pending', id='pilot', origin=[0., 0., 0.], bounds=[0., 0., 10., 10.],
                     sources=[frontage, dict(id='pilot:recipe', sha256=sha(recipe_raw), kind='scene-recipe', uncertaintyM=0.)],
                     materials=[road, wall], boundaries=[], supports=[], meshes=[mesh('ground', 'road'), mesh('facade', 'wall')],
                     fixtures=[], emitters=[], assets=[asset], lightOccluders=[],
                     coverage=dict(mode='replace', bounds=[0., 0., 10., 10.], entityIds=['B', 'facade', 'ground']),
                     collision={'c_0_0': dict(url=f'c_0_0.{sha(cell)[:16]}.col', sha256=sha(cell), bytes=len(cell), tris=1, h=[0., 1.])},
                     spawn=spawn)
        scene['release'] = release_hash(scene); self.scene = scene
        self.scene_path = self.scenes/'pilot.scene.json'
        self.scene_path.write_text(json.dumps(scene, separators=(',', ':'))+'\n')
        (self.scenes/scene['collision']['c_0_0']['url']).write_bytes(cell)
        self.catalog_path = self.scenes/'regions.json'
        self.catalog_path.write_text(json.dumps(dict(schema=REGIONS, defaultRegion='pilot', regions=[dict(
            id='pilot', label='Pilot', url='/scenes/pilot.scene.json', release=scene['release'], bounds=scene['bounds'], spawn=spawn)])))

    def tearDown(self):
        self.directory.cleanup()

    def publish(self, patch, **options):
        return publish(self.scene_path, self.recipe_path, dict(schema=SCHEMA, scene='pilot', **patch),
                       catalog_path=self.catalog_path, asset_root=self.store, **options)

    def load(self, directory=None):
        directory = directory or self.scenes
        return (json.loads((directory/'pilot.scene.json').read_text()), json.loads((directory/'pilot.recipe.json').read_text()),
                json.loads((directory/'regions.json').read_text()))

    def test_material_change_reuses_geometry_collision_and_reseals(self):
        road = dict(id='road', color=[1, 1, 1], roughness=.7, metalness=0)
        receipt = self.publish(dict(materials=[dict(material=road)]))
        scene, recipe, catalog = self.load()
        self.assertEqual(receipt['materials'], ['road']); self.assertEqual(receipt['rescaledMeshes'], 0)
        self.assertEqual(receipt['collisionCellsReused'], 1); self.assertIn('totalMs', receipt['timings'])
        self.assertEqual(scene['meshes'], self.scene['meshes'])
        self.assertEqual(next(m for m in scene['materials'] if m['id'] == 'road'), road)
        self.assertEqual(recipe['materialRecipes'], [dict(material=road, uvScale=[.5, .5])])
        self.assertEqual(scene['release'], release_hash(scene)); self.assertNotEqual(scene['release'], self.scene['release'])
        self.assertEqual(catalog['regions'][0]['release'], scene['release'])
        recipe_row = next(s for s in scene['sources'] if s['id'] == 'pilot:recipe')
        self.assertEqual(recipe_row['sha256'], sha(self.recipe_path.read_bytes()))
        self.assertEqual((self.scenes/scene['collision']['c_0_0']['url']).read_bytes(), self.cell)

    def test_uv_scale_rescales_only_that_materials_meshes(self):
        wall = dict(id='wall', color=[.5, .4, .3], roughness=.9, metalness=0)
        road = dict(id='road', color=[1, 1, 1], roughness=1, metalness=0)
        receipt = self.publish(dict(materials=[dict(material=road, uvScale=[1., 1.]), dict(material=wall, uvScale=[2., 4.])]))
        scene, recipe, _ = self.load()
        uv = {m['materialId']: m['uv'] for m in scene['meshes']}
        self.assertEqual(receipt['rescaledMeshes'], 2)
        self.assertEqual(uv['road'], [0., 0., 1., 0., 0., 1.])
        self.assertEqual(uv['wall'], [0., 0., 1., 0., 0., 2.])
        self.assertEqual([r['uvScale'] for r in recipe['materialRecipes']], [[1., 1.], [2., 4.]])
        self.assertEqual([m['positions'] for m in scene['meshes']], [m['positions'] for m in self.scene['meshes']])

    def test_rejects_unknown_targets_tampered_scenes_and_geometry_changes(self):
        material = dict(id='glass', color=[1, 1, 1], roughness=1, metalness=0)
        with self.assertRaisesRegex(ValueError, 'Unknown scene material'):self.publish(dict(materials=[dict(material=material)]))
        with self.assertRaisesRegex(ValueError, 'Unknown asset entity'):
            self.publish(dict(assets=[dict(entityId='missing', url=f'shared/{self.old_sha}.glb', sha256=self.old_sha)]))
        with self.assertRaisesRegex(ValueError, 'targets'):
            publish(self.scene_path, self.recipe_path, dict(schema=SCHEMA, scene='other', materials=[]), asset_root=self.store)
        with self.assertRaisesRegex(ValueError, 'changes nothing'):self.publish(dict(materials=[]))
        moved = copy.deepcopy(self.scene); moved['meshes'][0]['positions'][0] = .1
        with self.assertRaisesRegex(AssertionError, 'mesh ground'):check_appearance_only(self.scene, moved, {'road'})
        relit = copy.deepcopy(self.scene); relit['emitters'] = [dict(id='lamp')]
        with self.assertRaisesRegex(AssertionError, 'emitters'):check_appearance_only(self.scene, relit, set())
        tampered = copy.deepcopy(self.scene); tampered['materials'][1]['roughness'] = .1
        self.scene_path.write_text(json.dumps(tampered))
        with self.assertRaisesRegex(ValueError, 'sealed release'):
            self.publish(dict(materials=[dict(material=dict(id='road', color=[1, 1, 1], roughness=.5, metalness=0))]))

    def test_asset_replacement_requires_closure_and_updates_provenance(self):
        new = glb('new'); new_sha = sha(new)
        patch = dict(assets=[dict(entityId='B', url=f'shared/{new_sha}.glb', sha256=new_sha)])
        with self.assertRaises(FileNotFoundError):self.publish(patch)
        with self.assertRaisesRegex(ValueError, 'content address'):
            self.publish(dict(assets=[dict(entityId='B', url='shared/other.glb', sha256=new_sha)]))
        (self.store/'shared'/(new_sha+'.glb')).write_bytes(new)
        receipt = self.publish(patch)
        scene, recipe, _ = self.load()
        self.assertEqual(receipt['assets'], ['B'])
        self.assertEqual(scene['assets'][0]['sha256'], new_sha); self.assertEqual(recipe['assets'][0]['sha256'], new_sha)
        self.assertEqual(next(s for s in scene['sources'] if s['id'] == 'building:B:frontage')['sha256'], new_sha)
        self.assertEqual(recipe['assetSources'][0]['sha256'], new_sha)

    def test_separate_output_leaves_sources_and_copies_collision(self):
        output = Path(self.directory.name)/'out'
        before = self.scene_path.read_bytes()
        self.publish(dict(materials=[dict(material=dict(id='wall', color=[.6, .4, .3], roughness=.9, metalness=0))]), output=output)
        self.assertEqual(self.scene_path.read_bytes(), before)
        scene, recipe, catalog = self.load(output)
        self.assertEqual((output/scene['collision']['c_0_0']['url']).read_bytes(), self.cell)
        self.assertEqual(recipe['materialRecipes'][-1]['uvScale'], [1, 1])
        self.assertEqual(catalog['regions'][0]['release'], scene['release'])

    def test_apply_patch_does_not_mutate_its_input(self):
        original = copy.deepcopy(self.scene)
        recipe = json.loads(self.recipe_path.read_text())
        apply_patch(self.scene, recipe, dict(schema=SCHEMA, scene='pilot', materials=[dict(
            material=dict(id='road', color=[1, 1, 1], roughness=.2, metalness=0), uvScale=[1., 1.])]), self.store)
        self.assertEqual(self.scene, original)


if __name__ == '__main__':
    unittest.main()
