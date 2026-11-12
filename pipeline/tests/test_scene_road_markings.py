import copy
import hashlib
from pathlib import Path
import pickle
import tempfile
import unittest
import shapely
from turku3d.scene.road_markings import load,append


class RoadMarkingsTests(unittest.TestCase):
    def test_source_pin_clipping_and_receiver_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tile=root/'t0_0_0';tile.mkdir();path=tile/'markings.source.pkl'
            raw=pickle.dumps(dict(value=[shapely.box(.2,.2,1.2,.4),shapely.box(5,5,6,6)]));path.write_bytes(raw)
            polygons,sources=load(root,[0,0,1,1])
        self.assertEqual(len(polygons),1);self.assertAlmostEqual(polygons[0].area,.16)
        self.assertEqual(sources[0]['sha256'],hashlib.sha256(raw).hexdigest())
        mesh=dict(id='road',entityId='road',materialId='asphalt',positions=[0,1,0,1,1,0,1,1,1,0,1,1],indices=[0,2,1,0,3,2],collision='solid',supportId='support',normals=[0,1,0]*4,bakedVisibility=[150]*12,nightIrradiance=[.2]*12)
        support=dict(id='support',kind='road',meshId='road')
        scene=dict(materials=[dict(id='asphalt')],meshes=[mesh],supports=[support]);original=copy.deepcopy(scene)
        append(scene,polygons)
        self.assertEqual(scene['meshes'][0],original['meshes'][0]);self.assertEqual(scene['supports'],original['supports'])
        self.assertEqual(len(scene['meshes']),2);paint=scene['meshes'][1]
        self.assertEqual(paint['collision'],'passable');self.assertEqual(paint['supportId'],'support')
        self.assertTrue(all(abs(y-1.002)<1e-9 for y in paint['positions'][1::3]))
        self.assertTrue(all(abs(v-.2)<1e-9 for v in paint['nightIrradiance']))
