"""Offline checks for sharded fetch ledgers; run through the staged pipeline environment."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
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
 def test_import_does_not_patch_parent_registry(self):
  self.assertIs(prefetch.common.Registry,prefetch.OriginalRegistry)

if __name__=='__main__':unittest.main()
