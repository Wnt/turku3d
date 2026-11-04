"""bridge-ends@1: spot rebuild after the bridge-end rules (``coarse.bridge_join``, ``coarse.bridges``).

- A bridge's landward ramps are vertical curves that leave the deck at its grade and meet the
  ground at its grade (``bridge_join.vertical_curve``), long enough for 1.5 m/s^2 at 40 km/h, and
  they stop inside the tiles that build the bridge (``bridges.building_region``). The straight
  ramps' grade breaks were the jolts the drive sweep measured at every deck end.
- Deck lamps stand clear of the traffic lanes (``bridges.clear_of_lanes``).

Both change the ground, collision and deck furniture of the tiles that build a deck, which the
tile manifest counts in ``bridges``."""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    return bool(manifest.get("bridges"))


MIGRATION = Rebuild("bridge-ends", 1, affected=affected)
