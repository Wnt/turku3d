"""measured-walls@1: spot rebuild of the tiles holding a building with a measured wall colour.

``coarse.measured_walls`` reads the panorama colour run's confident wall colours (a farm source
overlay, ``derived/measured-walls/measured-walls.json``, never in git) and ``coarse.register``
gives each such building that paint instead of the register's material palette, below every
description, reading and reviewed patch and below a mapped OSM colour.

A spot rebuild reads the base's source version, which has no such table, so the affected class
is the tile list of the exported table: the tiles whose base manifest lists one of its
buildings. Version 1 is the export of 2026-11-13T23:24Z (849 of 1,539 measured buildings pass
confidence >= 0.5 and L* >= 45; 803 of them lie in these 61 tiles). The rebuilt tiles are
normal builds of this generator with the overlay source version (``tile-migrate register
--rebuild-revision``); every other tile keeps its payloads. A new export is a new version with
its own list.
"""

from .core import Rebuild

DATASET_SHA256 = "deb78db43d11c7393465f7790f85d9a3e379e21d4ade35ce29fd30fbe7da831c"
TILES = frozenset({
    "t0_-11_-1", "t0_-11_19", "t0_-11_20", "t0_-12_-1", "t0_-12_19", "t0_-1_-8", "t0_-1_0",
    "t0_-23_0", "t0_-23_1", "t0_-24_0", "t0_-24_1", "t0_-2_-1", "t0_-2_-19", "t0_-2_-2", "t0_-2_1",
    "t0_-3_19", "t0_-4_-3", "t0_-4_1", "t0_-4_19", "t0_-5_-3", "t0_-5_-6", "t0_-5_-7", "t0_-5_1",
    "t0_-6_1", "t0_-7_1", "t0_-9_-8", "t0_-9_-9", "t0_0_-8", "t0_0_0", "t0_0_1", "t0_10_-40",
    "t0_11_-40", "t0_11_-42", "t0_11_-43", "t0_12_-2", "t0_13_-10", "t0_13_-2", "t0_13_-9",
    "t0_14_-9", "t0_17_-7", "t0_18_-7", "t0_1_-1", "t0_1_0", "t0_1_23", "t0_1_24", "t0_21_-41",
    "t0_2_-4", "t0_2_0", "t0_4_-9", "t0_4_1", "t0_4_16", "t0_4_2", "t0_5_15", "t0_6_-37", "t0_6_0",
    "t0_6_3", "t0_6_4", "t0_7_0", "t0_7_4", "t0_9_-2", "t0_9_-3",
})


def affected(manifest: dict) -> bool:
    return manifest.get("tile") in TILES


MIGRATION = Rebuild("measured-walls", 1, affected=affected)
