"""stand-roof@1: spot rebuild after the grandstand canopy rule (``coarse.venues._canopy``).

Only grandstand meshes change, and a stand is built in the tile that owns it, so the tiles whose
venue statistics list a grandstand (or a generated stand) are the affected class."""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    venues = manifest.get("venues") or {}
    return bool(venues.get("grandstand_buildings") or venues.get("grandstands"))


MIGRATION = Rebuild("stand-roof", 1, affected=affected)
