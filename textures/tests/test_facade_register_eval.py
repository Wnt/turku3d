"""Coordinate contract for comparing LOD2 footprints with draft OSM shapes."""

import shapely
from turku3d import config
from turkutex.facade_register_eval import local_footprint


def test_osm_register_match_uses_draft_engine_axes():
    source = shapely.box(
        config.ORIGIN_E + 10,
        config.ORIGIN_N - 30,
        config.ORIGIN_E + 20,
        config.ORIGIN_N - 20,
    )
    assert local_footprint([source]).bounds == (10, 20, 20, 30)
