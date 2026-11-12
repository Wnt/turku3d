"""A compound podium clips the tower floor grid without shrinking its storeys."""
import unittest
from turku3d.facade_programs import storey_layout_height


class LayoutHeightTests(unittest.TestCase):
    def test_registered_tower_uses_supported_tall_wall_envelope(self):
        geometry=[dict(height_m=5.1615,fragment_width_m=100),
                  dict(height_m=17.746,fragment_width_m=10),
                  dict(height_m=21.49,fragment_width_m=40),
                  dict(height_m=23.256,fragment_width_m=5)]
        self.assertEqual(storey_layout_height(geometry,5.1615,dict(levels=7,levels_source='register')),21.49)

    def test_valid_grid_and_unknown_height_remain_source_owned(self):
        g=[dict(height_m=25.,fragment_width_m=100)]
        self.assertEqual(storey_layout_height(g,21.,dict(levels=7,levels_source='register')),21.)
        self.assertEqual(storey_layout_height(g,5.,dict(levels=7,levels_source='osm')),5.)
        self.assertEqual(storey_layout_height([],5.,dict(levels=7,levels_source='register')),5.)


if __name__=='__main__':unittest.main()
