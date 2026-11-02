"""Offline checks for sharded fetch ledgers; run through the staged pipeline environment."""
import importlib.util
import json
from pathlib import Path
import tempfile
import fcntl
from contextlib import ExitStack
import unittest
from unittest.mock import patch
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('bulk_prefetch',Path(__file__).with_name('prefetch.py'))
prefetch=importlib.util.module_from_spec(spec);spec.loader.exec_module(prefetch)

class Ledgers(unittest.TestCase):
 def test_existing_sources_read_without_rewriting_base(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'raw/laz';root.mkdir(parents=True)
   existing=root/'cached.laz';existing.write_bytes(b'baseline')
   base=prefetch.OriginalRegistry(root);base.record(existing,url='https://example.test/cached')
   before=base.manifest.read_bytes()
   prefetch.STATE=Path(tmp)/'state';prefetch.CONTEXT='lidar-one'
   shard=prefetch.ShardRegistry(root)
   self.assertTrue(shard.is_complete(existing,verify=True))
   added=root/'new.laz';added.write_bytes(b'new')
   shard.record(added,url='https://example.test/new')
   self.assertEqual(base.manifest.read_bytes(),before)
   self.assertEqual(set(shard.records()),{'new.laz'})
   self.assertEqual(shard.sums.read_text().count('\n'),1)
 def test_units_have_independent_locks_and_keep_checksums(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'raw/wfs';root.mkdir(parents=True)
   prefetch.STATE=Path(tmp)/'state';prefetch.CONTEXT='wfs-one';one=prefetch.ShardRegistry(root)
   prefetch.CONTEXT='wfs-two';two=prefetch.ShardRegistry(root)
   self.assertNotEqual(one.lockfile,two.lockfile)
   p=root/'a.gml';p.write_bytes(b'111');one.record(p,url='https://example.test/a')
   p.write_bytes(b'222')
   self.assertFalse(one.is_complete(p,verify=True))
   self.assertIsNone(two.get(p))
 def test_imagery_checks_every_quadrant_when_some_have_no_coverage(self):
  from turku3d.coarse import imagery
  tiles=[SimpleNamespace(id='tile-'+str(n)) for n in range(4)]
  visited=[];original=imagery.block_tiles
  def fetch(block,log):
   tile=imagery.block_tiles(block)[0];visited.append(tile.id)
   if tile.id in ('tile-0','tile-2'):raise prefetch.NoCoverage('observed blank imagery')
  with patch.object(prefetch,'block_tiles',return_value=tiles),patch.object(imagery,'fetch',side_effect=fetch):
   gaps=prefetch.fetch_imagery_block(None,lambda *_:None)
  self.assertEqual(visited,[t.id for t in tiles])
  self.assertEqual(set(gaps),{'tile-0','tile-2'})
  self.assertIs(imagery.block_tiles,original)
 def test_osm_extraction_holds_the_small_host_memory_slot(self):
  with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
   root=Path(tmp);state=root/'state';data=root/'data';data.mkdir()
   for name in ('receipts','registries','logs'):(state/name).mkdir(parents=True)
   for module in (prefetch.common,prefetch.laz,prefetch.wfs,prefetch.wms,prefetch.osm):
    stack.enter_context(patch.object(module,'Registry',prefetch.OriginalRegistry))
   stack.enter_context(patch.object(prefetch.C,'data_root',return_value=data))
   stack.enter_context(patch.object(prefetch.C,'raw_dir',side_effect=lambda name:data/'raw'/name))
   stack.enter_context(patch.object(prefetch,'osm_area',return_value=SimpleNamespace(name='clip')))
   def extract(area,log):
    with (state/'osm-extract.lock').open('a') as competing:
     with self.assertRaises(BlockingIOError):fcntl.flock(competing,fcntl.LOCK_EX|fcntl.LOCK_NB)
    destination=data/'raw/osm/clip';destination.mkdir(parents=True)
    (destination/'data.parquet').write_bytes(b'fixture')
   stack.enter_context(patch.object(prefetch.osm,'fetch_area',side_effect=extract))
   result=prefetch.fetch_job((str(state),'osm/23459500_6704500'))
   self.assertEqual(result['status'],'complete',result)
   with (state/'osm-extract.lock').open('a') as released:
    fcntl.flock(released,fcntl.LOCK_EX|fcntl.LOCK_NB)
 def test_import_does_not_patch_parent_registry(self):
  self.assertIs(prefetch.common.Registry,prefetch.OriginalRegistry)

if __name__=='__main__':unittest.main()
