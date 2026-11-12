import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import numpy as np
from turku3d.package.gltf import GlbMesh,build_glb,read_glb


class RetainedBuildingsTests(unittest.TestCase):
    def test_explicit_range_retains_attributes_and_source_binary(self):
        p=np.array([[0,1,0],[1,1,0],[0,2,0],[4,1,0],[5,1,0],[4,2,0]],dtype=float)
        mesh=GlbMesh('walls',p,np.array([[0,1,2],[3,4,5]]),uvs=np.zeros((6,2)),extras=dict(layer='buildings',objects=[
            dict(building_id='owned',start=0,count=3),dict(building_id='context',start=3,count=3)]))
        raw=build_glb([mesh]);original,binary=read_glb(raw)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'in.glb').write_bytes(raw);(root/'exclude.json').write_text('["owned"]')
            result=json.loads(subprocess.check_output(['node',str(Path(__file__).parents[1]/'turku3d/scene/retain_buildings.mjs'),str(root/'in.glb'),str(root/'out.glb'),str(root/'exclude.json')]))
            doc,new_binary=read_glb((root/'out.glb').read_bytes())
        self.assertEqual(result['buildings'],['context']);self.assertEqual(result['triangles'],1)
        self.assertEqual(new_binary[:len(binary)],binary)
        self.assertEqual(doc['meshes'][0]['primitives'][0]['attributes'],original['meshes'][0]['primitives'][0]['attributes'])
        self.assertEqual(result['bounds'],dict(min=[4.,1.,0.],max=[5.,2.,0.]))
        self.assertEqual(doc['nodes'][0]['extras']['objects'][0]['start'],0)
