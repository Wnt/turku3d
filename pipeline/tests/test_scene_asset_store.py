"""Authored hashes resolve from the canonical store without repository binaries."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from turku3d.scene.asset_store import resolve_asset,read_asset_url
from turku3d.scene.recipe import copy_assets


class AssetStoreTests(unittest.TestCase):
    def test_missing_repository_path_resolves_verified_shared_store_object(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'TURKU3D_DATA':directory}):
            raw=b'prepared-object';sha=hashlib.sha256(raw).hexdigest();root=Path(directory)/'prepared-assets';store=root/'shared';store.mkdir(parents=True);file=store/(sha+'.glb');file.write_bytes(raw)
            self.assertEqual(resolve_asset(Path(directory)/'old-repo/shared'/file.name),file.resolve())
            self.assertEqual(read_asset_url('https://unused.invalid/shared/'+file.name),raw)
            file.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):resolve_asset(Path(directory)/'old-repo/shared'/file.name)

    def test_missing_arbitrary_path_cannot_be_redirected_by_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'shared').mkdir();(root/'shared/source.glb').write_bytes(b'not-addressed')
            with self.assertRaises(FileNotFoundError):resolve_asset(root/'absent/source.glb',root)

    def test_recipe_closure_copies_maps_from_canonical_store(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'TURKU3D_DATA':directory}):
            root=Path(directory);store=root/'prepared-assets/shared';store.mkdir(parents=True);raw=b'map';sha=hashlib.sha256(raw).hexdigest();(store/(sha+'.png')).write_bytes(raw)
            recipe=dict(assets=[],materialRecipes=[dict(material=dict(baseColorTexture=dict(url='shared/'+sha+'.png',sha256=sha)))])
            self.assertEqual(copy_assets(recipe,root/'old-repo',root/'output'),1)
            self.assertEqual((root/'output/shared'/(sha+'.png')).read_bytes(),raw)
