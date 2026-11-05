"""facade-reality@1: spot rebuild after the source-driven facade rules (patrol 20261105 it3c).

Reviewed readings name a section's material by its own score (``stone: 0.99``) and building
family; a section's own kind decides blank vs windows, and a description's zero window count no
longer blanks a wall whose reading names windows; reviewed window heads replace period arches;
balconies in one named row count. Archetypes draw no stone without evidence, read the LOD2
function code under OSM ``building=yes`` and never put dwellings in the warehouse class, and
wood walls sample a multimodal paint mixture. Wood, use and stone rules reach ordinary buildings
throughout the city, so the affected class is every tile whose manifest lists buildings. The
rebuilt tiles are normal builds of this generator: parity by construction."""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    return bool(manifest.get("building_keys"))


MIGRATION = Rebuild("facade-reality", 1, affected=affected)
