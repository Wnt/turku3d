"""Source preservation, immutable identities and admission for distant derivatives."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scenery import Scenery


class SceneryTest(unittest.TestCase):
    def test_prepared_native_full_precedes_retained_facades_without_building_tiles(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);old=base/'old';native=base/'native';tile='t0_3_-1'
            for root,raw in ((old,b'old guessed facade'),(native,b'authored landmark')):
                directory=root/tile;directory.mkdir(parents=True)
                digest=hashlib.sha256(raw).hexdigest();(directory/'vis.glb').write_bytes(raw)
                (directory/'last-full.json').write_text(json.dumps(dict(pass_kind='full',visual={tile:dict(url='vis.glb',sha256=digest)})))
            cache=Scenery(old,base/'derived',preferred=native)
            row=cache.catalogue([tile])['tiles'][0]
            source=json.loads((cache.cache/f"{row['key']}.source.json").read_text())
            self.assertEqual(cache._source(source),(native/tile/'vis.glb').resolve())
            self.assertEqual(row['source_sha256'],hashlib.sha256(b'authored landmark').hexdigest())
            (native/tile/'last-full.json').unlink()
            self.assertNotEqual(cache.catalogue([tile])['tiles'][0]['key'],row['key'])
            cache.pool.shutdown()

    def test_a_farm_train_chain_serves_each_cell_from_the_newest_train_that_finished_it(self):
        from scenery import FarmChain
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); farm = base / 'farm'; tile = 't0_3_-1'
            def product(generation, raw, ready=True):
                directory = farm / 'products' / generation / tile; directory.mkdir(parents=True)
                (directory / 'vis.glb').write_bytes(raw)
                (directory / 'manifest.json').write_text(json.dumps(dict(pass_kind='full', visual={tile: dict(
                    url='vis.glb', sha256=hashlib.sha256(raw).hexdigest())})))
                if ready:
                    (directory / 'ready.json').write_text('{}')
            product('basegen', b'base')
            product('g1', b'train1')
            product('g2', b'train2', ready=False)  # train2 has not finished this tile
            product('g9', b'other base')
            for name, generation, base_generation in (('r1', 'g1', 'basegen'), ('r2', 'g2', 'basegen'),
                                                      ('r9', 'g9', 'elsewhere')):
                (farm / 'revisions' / name).mkdir(parents=True)
                (farm / 'revisions' / name / 'revision.json').write_text(json.dumps(dict(
                    kind='migrated', generation=generation, base_generation=base_generation)))
            chain = FarmChain(farm, 'http://farm', 'train', 'basegen')
            aliases = dict(train1='r1', train2='r2', train10='r9', other='r1')
            with patch.object(FarmChain, '_read', lambda self: FarmChain._members(self, aliases)):
                cache = Scenery(base / 'sources', base / 'derived', preferred=farm / 'products' / 'basegen', chain=chain)
                self.assertEqual([name for name, _ in chain.roots()], ['train2', 'train1'])  # train10: another base
                row = cache.catalogue([tile])['tiles'][0]
                source = json.loads((cache.cache / f"{row['key']}.source.json").read_text())
                self.assertEqual(cache._source(source), (farm / 'products' / 'g1' / tile / 'vis.glb').resolve())
                (farm / 'products' / 'g2' / tile / 'ready.json').write_text('{}')
                newer = cache.catalogue([tile])['tiles'][0]
                self.assertEqual(newer['source_sha256'], hashlib.sha256(b'train2').hexdigest())
                self.assertNotEqual(newer['key'], row['key'])  # a changed source is a new derivative, the rest stay
            cache.pool.shutdown()

    def test_empty_coastal_cell_falls_back_in_worker_without_catalogue_io(self):
        from types import SimpleNamespace
        from turku3d.coarse.sources import NoCoverage
        row = dict(tile='t0_-16_9',key='a'*64,kind='survey',source_sha256='source',h=[0,20],inputs=dict(ground=['sea.laz']))
        regional = dict(key='regional',inputs=dict(regional='dem.npz'))
        calls=[]
        survey = SimpleNamespace(regional_row=lambda tile:regional)
        with tempfile.TemporaryDirectory() as temp:
            cache=Scenery(temp,survey=survey);cache.cache.mkdir()
            (cache.cache/(row['key']+'.source.json')).write_text(json.dumps(row))
            def build(source,target):
                calls.append(source)
                if source['key']==row['key']:raise NoCoverage('no sea returns')
                target.write_bytes(b'ground');return dict(bytes=6,triangles=2)
            survey.build=build
            result=cache._build(row['key'])
            self.assertEqual(result['scenery_stats']['fallback'],'regional')
            self.assertEqual(len(calls),2)
            self.assertTrue((cache.cache/f"lod.{row['key']}.glb").is_file())
            cache.pool.shutdown()

    def test_a_tile_serves_its_newest_finished_build_marked_stale_while_its_new_key_builds(self):
        import threading
        from types import SimpleNamespace
        old = dict(tile='t2_0_0', key='a' * 64, kind='survey', source_sha256='old', h=[0, 20])
        new = dict(old, key='b' * 64, source_sha256='new')
        other = dict(old, tile='t2_1_0', key='c' * 64)
        gate = threading.Event()
        def build(source, target):
            if source['key'] != old['key']:
                gate.wait(10)
            target.write_bytes(source['key'].encode()); return dict(bytes=64, triangles=2)
        survey = SimpleNamespace(build=build)
        with tempfile.TemporaryDirectory() as temp:
            cache = Scenery(temp, survey=survey); cache.cache.mkdir()
            for row in (old, new, other):
                (cache.cache / (row['key'] + '.source.json')).write_text(json.dumps(row))
            self.assertEqual(cache.descriptor(old['key'])[0], 200)
            status, value = cache.descriptor(new['key'])  # building: the old build serves, stale
            self.assertEqual((status, value['stale'], value['visual']['t2_0_0']['url']), (200, True, f"../scenery/lod.{old['key']}.glb"))
            self.assertEqual(cache.descriptor(other['key'])[0], 202)  # nothing built for that tile yet
            # A restarted service finds the old build in its cache.
            restarted = Scenery(temp, survey=survey); restarted._index_built()
            self.assertTrue(restarted.descriptor(new['key'])[1]['stale'])
            gate.set()
            for future in list(cache.pending.values()) + list(restarted.pending.values()):
                future.result(10)
            status, value = cache.descriptor(new['key'])
            self.assertEqual((status, 'stale' in value, value['visual']['t2_0_0']['url']), (200, False, f"../scenery/lod.{new['key']}.glb"))
            cache.pool.shutdown(); restarted.pool.shutdown()

    def test_tree_sidecar_is_content_keyed_and_does_not_rebuild_or_change_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); folder=root/'t0_0_0';folder.mkdir()
            source=folder/'vis.glb';source.write_bytes(b'unchanged authored GLB')
            cache=Scenery(root,root/'cache');cache.cache.mkdir()
            key='c'*64
            (cache.cache/f'{key}.source.json').write_text(json.dumps(dict(tile=folder.name,source_url=source.name,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())))
            def extract(command,**kwargs):
                self.assertTrue(command[1].endswith('scenery-trees.mjs'))
                self.assertEqual(Path(command[2]),source.resolve())
                Path(command[3]).write_text('{"version":1,"nodes":[]}')
            with patch('scenery.subprocess.run',side_effect=extract) as run:
                self.assertEqual(cache.trees(key),(200,dict(version=1,nodes=[])))
                self.assertEqual(cache.trees(key)[0],200)
                self.assertEqual(run.call_count,1)
            self.assertEqual(source.read_bytes(),b'unchanged authored GLB')
            cache.pool.shutdown()

    def test_linked_tiles_resolve_external_buffers_against_the_original_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); original = root / 'original' / 't0_0_0'; original.mkdir(parents=True)
            source = original / 'vis.glb'; source.write_bytes(b'source with ../shared buffer')
            union = root / 'union'; union.mkdir(); (union / original.name).symlink_to(original)
            cache = Scenery(union, root / 'derivatives'); cache.cache.mkdir()
            key = 'b' * 64
            (cache.cache / f'{key}.source.json').write_text(json.dumps(dict(tile=original.name,
                source_url=source.name, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), h=[0, 20])))
            def convert(command, **kwargs):
                self.assertEqual(Path(command[2]), source.resolve())
                Path(command[3]).write_bytes(b'derivative')
                from types import SimpleNamespace
                return SimpleNamespace(stdout='{"bytes":10,"triangles":2}')
            with patch('scenery.subprocess.run', side_effect=convert):
                self.assertEqual(cache.descriptor(key)[0], 200)
            cache.pool.shutdown()

    def test_catalogue_preserves_sources_and_uses_retained_full_without_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 't0_-6_5'
            folder.mkdir()
            raw = b'precious source bytes'
            digest = hashlib.sha256(raw).hexdigest()
            name = f'vis.{digest}.glb'
            (folder / name).write_bytes(raw)
            full = dict(tile=folder.name, pass_kind='full', visual={folder.name: dict(url=name, sha256=digest, h=[0, 107])})
            (folder / 'last-full.json').write_text(json.dumps(full))
            (folder / 'manifest.json').write_text(json.dumps({**full, 'pass_kind': 'first'}))
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            cache = Scenery(root)
            first = cache.catalogue([folder.name, 't0_0_0'])
            self.assertEqual(first['missing'], ['t0_0_0'])
            self.assertEqual(len(first['tiles']), 1)
            self.assertEqual(cache.catalogue([folder.name])['tiles'], first['tiles'])
            key = first['tiles'][0]['key']
            self.assertTrue((cache.cache / f'{key}.source.json').is_file())
            cache.recipe = 'new derivative recipe'
            self.assertNotEqual(cache.catalogue([folder.name])['tiles'][0]['key'], key)
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            cache.pool.shutdown()

    def test_bad_source_hash_is_rejected_before_conversion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cache = Scenery(root); cache.cache.mkdir()
            folder = root / 't0_0_0'; folder.mkdir()
            name = 'vis.' + 'a' * 64 + '.glb'; (folder / name).write_bytes(b'changed')
            key = 'b' * 64
            (cache.cache / f'{key}.source.json').write_text(json.dumps(dict(tile=folder.name, source_url=name, source_sha256='a' * 64)))
            with patch('scenery.subprocess.run') as run:
                self.assertEqual(cache.descriptor(key)[0], 503)
                run.assert_not_called()
            self.assertEqual(cache.descriptor('../bad')[0], 404)
            cache.pool.shutdown()

    def test_cached_builds_use_an_isolated_derivative_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'sources'; folder = root / 't0_4_0'; folder.mkdir(parents=True)
            raw = b'cached complete build'; digest = hashlib.sha256(raw).hexdigest()
            (folder / 'vis.glb').write_bytes(raw)
            (folder / 'manifest.json').write_text(json.dumps(dict(coarse=True,
                visual={folder.name: dict(url='vis.glb', sha256=digest, h=[0, 30])})))
            cache = Scenery(root, Path(temp) / 'work' / 'derived')
            result = cache.catalogue([folder.name])
            self.assertEqual(len(result['tiles']), 1)
            self.assertFalse((root / '.scenery').exists())
            self.assertEqual((folder / 'vis.glb').read_bytes(), raw)
            self.assertTrue((cache.cache / (result['tiles'][0]['key'] + '.source.json')).exists())
            cache.pool.shutdown()


class PlacementSceneryTest(unittest.TestCase):
    def test_new_format_tiles_compose_their_building_models_into_the_derivative(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / 't0_2_-1'; folder.mkdir(); shared = root / 'shared'; shared.mkdir()
            raw = b'terrain'; digest = hashlib.sha256(raw).hexdigest(); name = f'vis.{digest}.glb'
            (folder / name).write_bytes(raw)
            model = b'whole building'; mid = hashlib.sha256(model).hexdigest(); (shared / f'{mid}.glb').write_bytes(model)
            lod = 'c' * 64
            placements = dict(version=1, models=[dict(id=mid, uri=f'../shared/{mid}.glb'), dict(id=lod, uri=f'../shared/{lod}.glb', kind='block-lod')],
                              placements=[dict(id='b', model=mid, position=[510, 0, -120])])
            full = dict(pass_kind='full', visual={folder.name: dict(url=name, sha256=digest)}, buildingPlacements=placements)
            (folder / 'manifest.json').write_text(json.dumps(full))
            cache = Scenery(root)
            row = cache.catalogue([folder.name])['tiles'][0]
            (folder / 'manifest.json').write_text(json.dumps({**full, 'buildingPlacements': None}))
            self.assertNotEqual(cache.catalogue([folder.name])['tiles'][0]['key'], row['key'])
            commands = []
            def convert(command, **kwargs):
                commands.append(command); Path(command[3]).write_bytes(b'glTF')
                return type('R', (), {'stdout': json.dumps(dict(bytes=4, triangles=2))})()
            with patch('scenery.subprocess.run', convert):
                cache._build(row['key'])
            spec = json.loads(Path(commands[0][4]).read_text())
            self.assertEqual(spec, [dict(path=str((shared / f'{mid}.glb').resolve()), position=[510, 0, -120])])
            (shared / f'{mid}.glb').write_bytes(b'edited')
            with patch('scenery.subprocess.run', convert), self.assertRaises(ValueError):
                cache._build(row['key'])
            cache.pool.shutdown()


class LodScenery(unittest.TestCase):
    def test_lod_mode_keys_authored_cells_on_survey_shells_and_new_cells_on_block_parents(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / 't0_0_0'; folder.mkdir(); shared = root / 'shared'; shared.mkdir()
            raw = b'authored'; digest = hashlib.sha256(raw).hexdigest(); name = f'vis.{digest}.glb'; (folder / name).write_bytes(raw)
            (folder / 'manifest.json').write_text(json.dumps(dict(pass_kind='full', visual={'t0_0_0': dict(url=name, sha256=digest)})))
            survey = SimpleNamespace(recipe='s', describe=lambda tile: dict(kind='survey', key='k' * 64, inputs=dict(buildings='/lod2.parquet')))
            plain, lod = Scenery(root, root / 'a', survey=survey), Scenery(root, root / 'b', survey=survey, lod=True)
            row = lod.catalogue(['t0_0_0'])['tiles'][0]
            self.assertNotEqual(row['key'], plain.catalogue(['t0_0_0'])['tiles'][0]['key'])
            source = json.loads((lod.cache / f"{row['key']}.source.json").read_text())
            self.assertEqual(source['shells'], dict(path='/lod2.parquet', key='k' * 64))
            parent = b'block parent'; pid = hashlib.sha256(parent).hexdigest(); (shared / f'{pid}.glb').write_bytes(parent)
            placements = dict(version=1, models=[dict(id=pid, uri=f'../shared/{pid}.glb', kind='block-lod')], placements=[],
                              blocks=[dict(id='b1', position=[10, 0, 20], lod=pid)])
            (folder / 'manifest.json').write_text(json.dumps(dict(pass_kind='full', visual={'t0_0_0': dict(url=name, sha256=digest)},
                                                                  buildingPlacements=placements)))
            row = lod.catalogue(['t0_0_0'])['tiles'][0]
            source = json.loads((lod.cache / f"{row['key']}.source.json").read_text())
            self.assertEqual(source['lods'], [dict(model=pid, uri=f'../shared/{pid}.glb', position=[10, 0, 20])])
            self.assertNotIn('shells', source)
            for cache in (plain, lod): cache.pool.shutdown()
