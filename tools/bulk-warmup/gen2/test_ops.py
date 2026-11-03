"""Offline source immutability, farm runner/resume, and acknowledged deletion checks."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import shutil
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import freeze
import transfer
from recipe import acquisition_recipe


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value)+'\n')


class Products(unittest.TestCase):
    def test_frozen_hardlink_survives_source_replacement_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir()
            (root/'data/raw/ortho').mkdir(parents=True)
            (root/'regional-city').mkdir();write(root/'regional-city/sources.json',{'test':True})
            source=root/'data/textures/a.bin';source.parent.mkdir();source.write_bytes(b'original')
            write(state/'prefetch-jobs.json',['a']);write(state/'walk.json',['t0_0_0'])
            write(state/'PREFETCH_COMPLETE.json',{'jobs_sha256':freeze.digest(state/'prefetch-jobs.json')})
            write(state/'acquisition.json',{'recipe':'recipe'})
            with patch.object(freeze,'acquisition_recipe',return_value='recipe'):
                report=freeze.freeze(root)
            profile=Path(report['data_root'])
            self.assertEqual(source.stat().st_ino,(profile/'textures/a.bin').stat().st_ino)
            new=source.with_suffix('.tmp');new.write_bytes(b'replaced');new.replace(source)
            self.assertEqual((profile/'textures/a.bin').read_bytes(),b'original')
            transfer.validate_sources(profile,report['version'])
            (profile/'textures/a.bin').write_bytes(b'corrupt!')
            with self.assertRaisesRegex(ValueError,'source hash mismatch'):
                transfer.validate_sources(profile,report['version'])

    def test_catalog_validates_inventory_and_overlays_but_explicitly_omits_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);overlay=root/'supplements/params.json'
            overlay.parent.mkdir();overlay.write_bytes(b'{}')
            files={'supplements/params.json':dict(bytes=2,sha256=hashlib.sha256(b'{}').hexdigest()),
                   'raw/laz/source.laz':dict(bytes=3,sha256=hashlib.sha256(b'raw').hexdigest())}
            version=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            write(root/'source-version.json',dict(version=version,files=files))
            transfer.validate_sources(root,version,raw=False)
            with self.assertRaises(FileNotFoundError):transfer.validate_sources(root,version)
            overlay.write_bytes(b'xx')
            with self.assertRaisesRegex(ValueError,'source hash mismatch'):
                transfer.validate_sources(root,version,raw=False)

    def test_catalog_can_be_materialized_to_full_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);remote=root/'remote';receiver=root/'receiver'
            files={'raw/a':dict(bytes=3,sha256=hashlib.sha256(b'raw').hexdigest())}
            version=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            origin=remote/'farm/sources/versions'/version;write(origin/'source-version.json',dict(version=version,files=files))
            (origin/'raw').mkdir();(origin/'raw/a').write_bytes(b'raw')
            final=receiver/'sources/versions'/version;write(final/'source-version.json',dict(version=version,files=files))
            receipt=receiver/'receipts/sources'/(version+'.json')
            write(receipt,dict(version=version,mode='catalog',verified=True,raw_available=False))
            class Local(transfer.Pull):
                def json(self,path):return json.loads(Path(path).read_text())
                def sync(self,source,dest,extra=()):shutil.copytree(source,dest,dirs_exist_ok=True)
                def room(self,needed):pass
            Local(SimpleNamespace(root=receiver,remote_root=str(remote),link_dest=None)).sources(version)
            transfer.validate_sources(final,version)
            self.assertTrue(json.loads(receipt.read_text())['raw_available'])
            self.assertEqual((final/'raw/a').read_bytes(),b'raw')

    def test_publication_follows_shared_glb_references_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tile='t0_0_0';product=root/tile;product.mkdir()
            shared=root/'shared';shared.mkdir();(shared/'map.ktx2').write_bytes(b'texture')
            def glb(uri):
                doc=json.dumps({'images':[{'uri':uri}]}).encode();doc+=b' '*(-len(doc)%4)
                (product/'vis.hash.glb').write_bytes(b'glTF'+struct.pack('<II',2,20+len(doc))+struct.pack('<II',len(doc),0x4e4f534a)+doc)
            glb('../shared/map.ktx2')
            write(product/'manifest.json',{'visual':{tile:{'url':'vis.hash.glb'}}})
            write(product/'ready.json',{'generation':'a'*64})
            (product/'vis.glb').write_bytes(b'unpublished duplicate')
            (product/'facade-programs.json').write_bytes(b'large rebuild-only report')
            files=transfer.publication(root,tile)
            self.assertEqual(set(files),{tile+'/manifest.json',tile+'/ready.json',tile+'/vis.hash.glb','shared/map.ktx2'})
            glb('../../outside')
            with self.assertRaisesRegex(ValueError,'outside generation'):transfer.publication(root,tile)

    def test_portable_vrt_preserves_an_earlier_frozen_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';ortho=data/'raw/ortho';ortho.mkdir(parents=True)
            image=ortho/'image.tif';image.write_bytes(b'pixels')
            vrt=ortho/'image.vrt'
            vrt.write_text('<VRTDataset><SourceFilename relativeToVRT="0">'+str(image)+'</SourceFilename></VRTDataset>')
            retained=Path(tmp)/'old.vrt';os.link(vrt,retained)
            freeze.portable_vrts(data)
            self.assertIn('relativeToVRT="1"',vrt.read_text())
            self.assertNotIn(str(image),vrt.read_text())
            self.assertIn(str(image),retained.read_text())
            self.assertNotEqual(vrt.stat().st_ino,retained.stat().st_ino)

    def test_acquisition_recipe_ignores_geometry_and_tracks_fetch_changes(self):
        import recipe
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);source=repo/'fetch.py';source.write_text('v1')
            with patch.object(recipe,'FILES',('fetch.py',)):
                before=acquisition_recipe(repo)
                (repo/'renderer.py').write_text('new visuals')
                self.assertEqual(before,acquisition_recipe(repo))
                source.write_text('v2')
                self.assertNotEqual(before,acquisition_recipe(repo))

    def test_bulk_cache_pressure_preserves_unacknowledged_older_generation(self):
        sys.path.insert(0,str(Path(os.environ['REPO'])/'scripts/dev'))
        from shared_tiles import Farm
        from runtime import preserve_products
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);generation='a'*64;revision='b'*40
            write(root/'revisions'/revision/'revision.json',dict(revision=revision,generation=generation))
            product=root/'products'/generation/'t0_0_0'
            write(product/'ready.json',dict(revision=revision,generation=generation))
            farm=Farm(root,root/'runner',free=lambda:0,grace_seconds=0)
            try:
                with farm.lock:
                    self.assertFalse(preserve_products(farm,farm._make_room,1))
                    self.assertTrue(product.exists());self.assertEqual(farm.aliases,{})
                    # The ordinary LAN eviction policy would remove this unaliased
                    # generation, proving the bulk-specific pin changes the outcome.
                    farm._make_room(1)
                    self.assertFalse(product.exists())
            finally:farm.close()

    def test_prune_requires_matching_verified_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);gen='a'*64;tile='t0_0_0'
            product=root/'farm/products'/gen/tile
            write(product/'manifest.json',{'tile':tile})
            write(product/'ready.json',{'generation':gen,'revision':'b'*64})
            with self.assertRaises(FileNotFoundError):transfer.prune(root,gen,tile)
            ack=transfer.verified_ack(product,gen,tile,'receiver')
            path=root/'farm/exported'/gen/(tile+'.json');write(path,{**ack,'manifest_sha256':'0'*64})
            with self.assertRaisesRegex(ValueError,'changed'):transfer.prune(root,gen,tile)
            self.assertTrue(product.exists())
            write(path,ack);transfer.prune(root,gen,tile)
            self.assertFalse(product.exists());self.assertTrue(path.exists())
            write(product/'manifest.json',{'tile':tile})
            write(product/'ready.json',{'generation':gen,'revision':'b'*64})
            write(product.parent/'warm.json',{'channels':10})
            transfer.prune(root,gen,tile)
            self.assertTrue(product.exists(), 'verified warm seeds survive streaming/resume')

    def test_farm_runner_resumes_and_obeys_prefetch_interlock(self):
        repo=Path(os.environ['REPO']).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();(root/'repo').symlink_to(repo)
            commit='a'*40;gen='b'*64;version=hashlib.sha256(b'{}').hexdigest()
            write(state/'revision.json',dict(commit=commit,revision=commit,generation=gen))
            base=root/'farm/revisions'/commit;base.mkdir(parents=True)
            (base/'repo').symlink_to(repo)
            write(base/'revision.json',dict(commit=commit,revision=commit,generation=gen))
            write(root/'farm/sources/versions'/version/'source-version.json',dict(version=version,files={}))
            write(state/'walk.json',['t0_0_0','t0_1_0'])
            write(state/'PREFETCH_COMPLETE.json',dict(jobs_sha256='jobs',unavailable_tiles={}))
            write(state/'sources.json',dict(version=version,prefetch_jobs_sha256='jobs',recipe=acquisition_recipe(repo),walk_sha256=freeze.digest(state/'walk.json')))
            # A deterministic stand-in exercises the real farm scheduler, file locks,
            # GLB external references, validation and atomic ready publication.
            generator=root/'fake.py'
            generator.write_text('''import sys,json,hashlib,struct
from pathlib import Path
def build(out):
 out.mkdir(parents=True,exist_ok=True)
 shared=out.parent/'shared';shared.mkdir(exist_ok=True)
 texture=b'test texture';sha=hashlib.sha256(texture).hexdigest();name='texture.'+sha+'.ktx2';(shared/name).write_bytes(texture)
 doc=json.dumps({'asset':{'version':'2.0'},'images':[{'uri':'../shared/'+name}]}).encode();doc+=b' '*(-len(doc)%4)
 glb=b'glTF'+struct.pack('<II',2,20+len(doc))+struct.pack('<II',len(doc),0x4e4f534a)+doc
 (out/'vis.glb').write_bytes(glb)
 visual={'url':'vis.glb','bytes':len(glb),'sha256':hashlib.sha256(glb).hexdigest()}
 _,i,j=out.name.split('_');collision={}
 for x in range(4):
  for z in range(4):
   c=f'c_{int(i)*4+x}_{int(j)*4+z}';(out/(c+'.col')).write_bytes(b'col')
   collision[c]={'url':c+'.col','bytes':3,'sha256':hashlib.sha256(b'col').hexdigest()}
 (out/'manifest.json').write_text(json.dumps({'coarse':True,'tile':out.name,'pass_kind':'full','visual':{out.name:visual},'collision':collision}))
if 'worker' in sys.argv:  # the bulk farm's persistent warm worker protocol
 for line in sys.stdin:
  build(Path(json.loads(line)['out']));print(json.dumps({'code':0,'stdout':'{}'}),flush=True)
else:
 build(Path(sys.argv[sys.argv.index('--out')+1]))
''')
            runner=root/'run.sh';runner.write_text('#!/bin/sh\nexec '+sys.executable+' '+str(generator)+' "$@"\n');runner.chmod(0o755)
            command=[sys.executable,str(HERE/'bulk.py'),str(root),'--tiles','t0_0_0','t0_1_0','--workers','1','--reserve-gib','0.01','--worker-memory-gib','0.01']
            with (state/'prefetch.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                blocked=subprocess.run(command,capture_output=True,text=True,timeout=20)
                self.assertNotEqual(blocked.returncode,0)
                self.assertIn('BlockingIOError',blocked.stderr)
                self.assertFalse((state/'build.json').exists())
            first=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(first.returncode,0,first.stderr)
            report=json.loads(first.stdout.splitlines()[-1]);self.assertEqual(report['metrics']['builds'],2)
            second=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(second.returncode,0,second.stderr)
            report=json.loads(second.stdout.splitlines()[-1]);self.assertEqual(report['already_complete'],2)
            # A receiver/producer must reject a corrupt shared dependency, even
            # though the tile manifest and its own GLB checksum still match.
            shared=next((root/'farm/products').glob('*/shared/texture.*'))
            shared.write_bytes(b'corrupt')
            third=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(third.returncode,0)
            self.assertIn('payload hash mismatch',third.stderr)

if __name__=='__main__':unittest.main()
