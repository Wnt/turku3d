"""Visual foliage must retain measured contacts and deliberate path openings."""
from copy import deepcopy
import unittest
import numpy as np
from turku3d.scene.compile import mesh_record
from turku3d.scene.hedges import segment_geometry
from turku3d.scene.hedge_foliage import append, geometry


def solid(start=0., end=1.):
    p, ix, uv, support = segment_geometry(
        np.array([start, 0.]), np.array([end, 0.]), .65, None, .8,
        lambda x, z, **kw: (dict(id='supported-road'), 3.+.1*x+.03*z))
    return mesh_record('hedge:'+str(start), 'mapped-hedge', 'hedge', p, ix,
                       support=support, uv=uv)


class HedgeFoliageTests(unittest.TestCase):
    def test_opening_endpoints_and_sloped_envelope_are_preserved(self):
        core = solid()
        faces = np.asarray(core['positions']).reshape(6, 6, 3)[:, [0, 2, 1, 4]]
        p, ix, uv, normals = geometry(faces, core['id'])
        self.assertGreaterEqual(p[:, 0].min(), 0.)
        self.assertLessEqual(p[:, 0].max(), 1.)
        self.assertLessEqual(np.abs(p[:, 2]).max(), .375+1e-12)
        height = p[:, 1]-(3.+.1*p[:, 0]+.03*p[:, 2])
        self.assertGreaterEqual(height.min(), -.002)
        self.assertLessEqual(height.max(), .852)
        self.assertTrue(np.isfinite(p).all() and np.isfinite(normals).all())
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1., atol=1e-12)
        self.assertTrue(np.all((uv >= 0) & (uv <= 1)))
        self.assertEqual(ix.shape[1], 3)

    def test_connected_segments_overlap_without_replacing_physics(self):
        cores = [solid(), solid(1., 2.)]
        scene = dict(meshes=deepcopy(cores), materials=[{'id':'hedge'}], sources=[])
        added = append(scene)
        self.assertEqual(len(added), 2)
        for old, retained in zip(cores, scene['meshes'][:2]):
            self.assertEqual({k:v for k,v in retained.items() if k not in ('render','lightOcclusion')}, old)
            self.assertFalse(retained['render'])
            self.assertEqual(retained['lightOcclusion'], 'none')
        left, right = scene['meshes'][2:4]
        for leaf, proxy in zip((left,right), scene['meshes'][4:]):
            self.assertEqual(proxy['positions'], leaf['positions'][:54])
            self.assertEqual(proxy['lightOcclusion'], 'solid')
            self.assertFalse(proxy['render'])
            self.assertEqual(proxy['collision'], 'passable')
        self.assertGreater(max(left['positions'][::3]), 1.)
        self.assertLess(min(right['positions'][::3]), 1.)
        self.assertGreaterEqual(min(left['positions'][::3]), 0.)
        self.assertLessEqual(max(right['positions'][::3]), 2.)
        self.assertTrue(all(m['collision']=='passable' for m in (left,right)))
        repeated = dict(meshes=deepcopy(cores), materials=[{'id':'hedge'}], sources=[])
        append(repeated)
        self.assertEqual(scene, repeated)


if __name__ == '__main__':
    unittest.main()
