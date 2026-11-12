"""The registered entrance is one geometry, support and collision product."""
import unittest
from turku3d.scene.compile import mesh_record
from turku3d.scene.qwensel_compile import append_qwensel
from turku3d.scene.qwensel_details import QWENSEL_FRONTAGE as D


class QwenselCompileTests(unittest.TestCase):
    def scene(self):
        ground=mesh_record('ground','ground','ground',[[200,3.8,225],[215,3.8,225],[215,3.8,240],[200,3.8,240]],[[0,2,1],[0,3,2]],support='ground-support')
        return dict(assets=[dict(entityId=D['buildingId'],sha256='adapted')],
            sources=[dict(id='building:'+D['buildingId']+':authored',sha256=D['authoredGlbSha256'],kind='approved-building'),
                     dict(id='building:'+D['buildingId']+':frontage',sha256='adapted',kind='registered-frontage-appearance')],
            materials=[],meshes=[ground],supports=[dict(id='ground-support',kind='footway',meshId='ground',boundaryIds=[])],boundaries=[],fixtures=[])

    def test_stair_top_proxies_share_solid_vertices_without_duplicate_collision(self):
        scene=self.scene();append_qwensel(scene)
        solids=[m for m in scene['meshes'][1:] if m['collision']=='solid']
        self.assertEqual(len(solids),3)
        proxies=[m for m in scene['meshes'][1:] if m.get('render') is False]
        self.assertEqual(len(proxies),15)
        vertices={tuple(round(v,7) for v in m['positions'][i:i+3]) for m in solids for i in range(0,len(m['positions']),3)}
        for proxy in proxies:
            self.assertEqual(proxy['collision'],'passable')
            self.assertTrue(all(tuple(round(v,7) for v in proxy['positions'][i:i+3]) in vertices for i in range(0,len(proxy['positions']),3)))
        fixture=scene['fixtures'][0]
        self.assertEqual(fixture['supportId'],'ground-support')
        self.assertEqual(len(fixture['meshIds']),4)
        self.assertEqual(len(scene['supports']),16)
        for support in scene['supports'][1:]:self.assertEqual(len(support['boundaryIds']),4)

    def test_unregistered_appearance_cannot_attach_corrected_openings(self):
        scene=self.scene();scene['sources'].pop();append_qwensel(scene)
        self.assertEqual(len(scene['meshes']),1);self.assertFalse(scene['fixtures'])
