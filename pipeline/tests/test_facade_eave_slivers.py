"""A zero-width subtraction remnant must not erase an otherwise surveyed facade."""
import unittest
import numpy as np
from turku3d.coarse.stored_facades import ring_eaves
from turku3d.facade_programs import storey_layout_height


def quad(a,b,top_a,top_b,base=8.5):
    return [[a[0],base,a[1]],[b[0],base,b[1]],
            [b[0],top_b,b[1]],[a[0],top_a,a[1]]]


class EaveSliverTests(unittest.TestCase):
    def test_clipped_micrometre_return_does_not_collapse_ring(self):
        # Reproduce the held-out wall clipping shape: a tip touches the base,
        # although the adjacent20m facade reaches a measured14.5m eave.
        walls=np.array([quad((0,0),(20,0),16.8,14.5),
                        quad((20,0),(20,1e-7),14.5,8.5),
                        quad((20,1e-7),(20,10),14.5,14.5),
                        quad((20,10),(0,10),14.5,16.8),
                        quad((0,10),(0,0),16.8,16.8)]).reshape(-1,3)
        original=walls.copy();heights=ring_eaves(walls)
        self.assertEqual(set(heights.values()),{14.5})
        np.testing.assert_array_equal(walls,original)
        # The normal registered-storey rule can still use a real lower wall
        # envelope; neither the roof ridge nor the repaired ring invents floors.
        g=[dict(height_m=6.,fragment_width_m=50),dict(height_m=4.85,fragment_width_m=10)]
        self.assertEqual(storey_layout_height(g,6.,dict(levels=1,levels_source='register')),4.85)

    def test_short_real_return_and_ordinary_gable_keep_their_eave(self):
        walls=np.array([quad((0,0),(4,0),14,12),quad((4,0),(4,.02),12,11),
                        quad((4,.02),(0,0),11,14)]).reshape(-1,3)
        self.assertEqual(set(ring_eaves(walls).values()),{11.})
        other=np.array([quad((0,0),(5,0),16,13),quad((5,0),(5,4),13,13),
                        quad((5,4),(0,0),13,16)]).reshape(-1,3)
        self.assertEqual(set(ring_eaves(other).values()),{13.})


if __name__=='__main__':unittest.main()
