"""bridge-ends@1: spot rebuild after the bridge-end rules (``coarse.bridge_join``, ``coarse.bridges``).

- A bridge's landward ramps are vertical curves that leave the deck at its grade and meet the
  ground at its grade (``bridge_join.vertical_curve``), long enough for 1.5 m/s^2 at 40 km/h, and
  they stop inside the tiles that build the bridge (``bridges.building_region``). The straight
  ramps' grade breaks were the jolts the drive sweep measured at every deck end.
- Deck lamps stand clear of the traffic lanes (``bridges.clear_of_lanes``).

They change the ground, collision and deck furniture of the tiles that build a deck (the manifest's
``bridges`` count) and of the tiles that only hold a lower road or a ramp within reach of a road
bridge's deck (``bridge_ends_tiles``, from the base generation's street graphs)."""

from .bridge_ends_tiles import TILES
from .core import Rebuild


def affected(manifest: dict) -> bool:
    return bool(manifest.get("bridges")) or manifest.get("tile") in TILES


MIGRATION = Rebuild("bridge-ends", 1, affected=affected)
