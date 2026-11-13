"""town-premises@1: spot rebuild of the tiles holding a town building whose customer premises open it.

``coarse.milieus`` gives every building the type of the centre milieu zone holding it (a farm source
overlay, ``derived/milieus/milieus.json``, never in git) and ``coarse.premises`` opens the street
storey of a town building (``coarse/town-milieus.json``) on the walls where its mapped restaurants,
cafes, bars, pubs, banks and pharmacies open, below descriptions, readings and reviewed walls.

A spot rebuild reads the base's source version, which has no milieu table, so the affected class
is a committed tile list: the owning tiles of the 70 buildings that the rule opens and nothing
describes (``work/survey`` of pp-town over the 178 tiles of the milieu area, 2026-11-13T23:50Z, the
pipeline's own enrichment on the farm's OSM snapshot work-3x3-261024). A changed town set, OSM
snapshot or rule is a new version with its own list.
"""

from .core import Rebuild

TILES = frozenset({
    "t0_-9_6", "t0_-8_6", "t0_-5_3", "t0_-4_0", "t0_-4_2", "t0_-3_0", "t0_-3_2", "t0_-2_-2", "t0_-2_0",
    "t0_-2_1", "t0_-2_5", "t0_-1_-2", "t0_-1_-1", "t0_-1_0", "t0_-1_1", "t0_-1_5", "t0_0_-3",
    "t0_0_-1", "t0_0_0", "t0_0_1", "t0_0_2", "t0_1_-2", "t0_1_-1", "t0_1_0", "t0_1_1", "t0_2_-2",
    "t0_2_-1", "t0_2_0", "t0_2_1", "t0_2_2", "t0_3_-3", "t0_3_-1", "t0_3_0", "t0_5_-1", "t0_5_2",
})


def affected(manifest: dict) -> bool:
    return manifest.get("tile") in TILES


MIGRATION = Rebuild("town-premises", 1, affected=affected)
