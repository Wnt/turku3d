"""finished-ground@1: spot rebuild after the finished-ground rule (``turku3d.ground.finished``).

The 2021 city lidar caught streets and squares rebuilt around then (Kauppatori 2019-2022)
mid-construction. The rule replaces that ground, inside street parts the city register dates to
the lidar's year or later, with the finished surface (spot heights surveyed after the lidar, the
surrounding lidar); the generator's ground input carries it, so terrain, collision, kerbs, lane
heights and everything placed on the ground follow. The affected class is the tiles whose ground
input changes under the rule: the same function the generator runs decides it, from the base's
source version, so a rebuilt tile is a normal build of this generator (parity by construction)
and every other tile keeps its payloads byte for byte.
"""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    if "finished_ground" in manifest:  # built by a generator that already has the rule
        return False
    from turku3d import config as C
    from turku3d.coarse.inputs import finished_ground, finished_sources
    from turku3d.coarse.sources import NoCoverage, Sources

    tile = C.VisualTile.parse(manifest["tile"])
    sources = Sources()
    try:
        laz, missing = sources.lidar_paths(tile)
    except NoCoverage:
        return False
    if missing:
        return False
    return finished_ground(tile, finished_sources(tile, sources, laz))["report"] is not None


MIGRATION = Rebuild("finished-ground", 1, affected=affected)
