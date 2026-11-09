"""finished-ground@2: spot rebuild after the finished-ground rule (``turku3d.ground.finished``).

The 2021 city lidar caught streets and squares rebuilt around then (Kauppatori 2019-2022)
mid-construction. The rule replaces that ground, inside street parts the city register dates to
the lidar's year or later, with the finished surface (spot heights surveyed after the lidar, the
surrounding lidar); the generator's ground input carries it, so terrain, collision, kerbs, lane
heights and everything placed on the ground follow. The affected class is the tiles whose ground
input changes under the rule: the same function the generator runs decides it, from the base's
source version, so a rebuilt tile is a normal build of this generator (parity by construction)
and every other tile keeps its payloads byte for byte.

@2: tunnels, underpasses, bridge ramps and the street parts leading into them (their first 40 m) keep
their relief; @1 filled the Myllyahde tunnel approach by up to 9 m. A tile @1 changed is rebuilt again.
"""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    if manifest.get("finished_ground"):  # an earlier rule (or this one) changed this tile's ground
        return True
    if "finished_ground" in manifest and manifest.get("finished_ground_rule") == RULE:
        return False  # built by this rule, which left the ground alone
    from turku3d import config as C
    from turku3d.coarse.inputs import _ABSENT, _entry, _input_lock, _load, _prepare_one, _save, finished_sources
    from turku3d.coarse.sources import NoCoverage, current_sources

    tile = C.VisualTile.parse(manifest["tile"])
    sources = current_sources()
    try:
        laz, missing = sources.lidar_paths(tile)
    except NoCoverage:
        return False
    if missing:
        return False
    # The generator's own prepared dataset (same key, same cache): the spot rebuild reuses it.
    paths = finished_sources(tile, sources, laz)
    path, key = _entry(C.work_root() / "build" / "coarse", tile, "finished_ground", paths)
    with _input_lock(path):
        value = _load(path, key)
        if value is _ABSENT:
            value = _prepare_one(tile, "finished_ground", paths)
            _save(path, key, value)
    return value["report"] is not None


RULE = 2
MIGRATION = Rebuild("finished-ground", RULE, affected=affected)
