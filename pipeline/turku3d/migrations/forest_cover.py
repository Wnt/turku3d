"""forest-cover@1: spot rebuild of the tiles whose forest the pre-2c155e61 forest rules cut short.

Two rules of that generator removed real trees, and both leave a mark in the manifest:

- Its tree cap (768) cut a closed forest's crown inventory. Such a tile records ``trees`` at or just below its
  ``vegetation.tree_cap``. Rebuilt with the 2048 cap, those tiles draw a median 1,478 trees (natureref survey,
  2026-11-09).
- It filled a closed track ring into an open playing surface that blocked every crown inside. Nunnavuori's kuntorata
  (``leisure=track`` + ``highway=footway``) left 58 and 100 trees on two tiles where the lidar puts 0.96-0.99 canopy;
  rebuilt, they draw 1,544 and 1,696. The venues stage counts such a ring in ``venues.tracks``.

Products of the fixed generator record a 2048 cap, so the step holds only on the old products. Below the cap, without
a track, the old and new tree counts agree (median ratio 0.99)."""

from .core import Rebuild

OLD_CAP = 768  # coarse.forests.TREE_CAP before 2c155e61
CUT = 8  # trees a cut inventory can lose after the cap (clearance against paths and buildings)


def affected(manifest: dict) -> bool:
    cap = (manifest.get("vegetation") or {}).get("tree_cap")
    if not cap or cap > OLD_CAP:
        return False
    tracks = (manifest.get("venues") or {}).get("tracks") or 0
    return manifest.get("trees", 0) >= cap - CUT or tracks > 0


MIGRATION = Rebuild("forest-cover", 1, affected=affected)
