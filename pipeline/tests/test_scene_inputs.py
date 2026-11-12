"""Complete entities crossing a bounded region retain source collision authority."""
from pathlib import Path
import pickle
import tempfile
from types import SimpleNamespace
import unittest
import numpy as np
import shapely
from turku3d.scene.compile import load_evidence


class SourceTests(unittest.TestCase):
    def test_cross_boundary_context_is_promoted_without_replacing_owned_shell(self):
        owned=SimpleNamespace(key='owned',footprint=shapely.box(2,2,3,3))
        crossing=SimpleNamespace(key='crossing',footprint=shapely.box(-5,2,1,3))
        outside=SimpleNamespace(key='outside',footprint=shapely.box(-5,2,-1,3))
        duplicate=SimpleNamespace(key='owned',footprint=shapely.box(4,4,5,5))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'t0_0_0';root.mkdir()
            for name,value in [('ground',dict(points=np.array([[0.,0.,0.],[10.,0.,0.],[0.,0.,10.]]))),
                ('wfs',dict(buildings=[owned],wall_context=[duplicate,crossing,outside],streets=[],water=[]))]:
                (root/(name+'.frozen.pkl')).write_bytes(pickle.dumps(dict(value=value)))
            buildings=load_evidence(Path(directory),[0,0,10,10])[1]
        result={b.key:b for b in buildings}
        self.assertEqual(set(result),{'owned','crossing'})
        self.assertTrue(result['owned'].footprint.equals(owned.footprint))
        self.assertTrue(result['crossing'].footprint.equals(crossing.footprint))


if __name__=='__main__':unittest.main()
