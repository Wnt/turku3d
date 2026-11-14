"""review-patches@1: spot rebuild of the tiles holding a reviewed patch from the 2026-11-13 review.

Three places proven drew unlike the street photographs, fixed in proven's own data:
* Kristiinankatu 1 (issue #2) draws its granite ground storey with round-arched openings, its granite
  fields and tower bays and its rendered end bays and top storey from a reviewed landmark description
  (``landmarks/descriptions``) whose elevations carry ``stone_zones``; ``landmarks.elevations`` paints
  those zones with the description's stone and gives each wall piece the row on its nearest plane, so
  courtyard walls keep their own plain rows.
* Linnankatu 29 (issue #10) has reviewed program walls (``textures/library/facade-programs.json``): four
  windows and two doors on the street wall, a blank end wall; reviewed walls outrank its AI description.
* The Vähätori lion fountain (issue #6) draws its centrepiece (``coarse.fountains``, record in
  ``coarse/fountains.json``) in its mapped basin, and no lidar tree stands in a mapped basin any more
  (``coarse.forests``).

The affected class is the tiles whose manifest lists a building of ``review_patches_keys.json`` and the
tiles holding a reviewed fountain. Other tiles with a basin get the trunk rule at their next rebuild.
The rebuilt tiles are normal builds of this generator: parity by construction. A new set of reviewed
patches is a new version of this step.
"""

import json
from functools import lru_cache
from pathlib import Path

from .core import Rebuild

KEYS = Path(__file__).with_name("review_patches_keys.json")


@lru_cache(maxsize=1)
def keys() -> frozenset:
    return frozenset(json.loads(KEYS.read_text())["keys"])


def affected(manifest: dict) -> bool:
    from turku3d.coarse.fountains import tiles

    return manifest.get("tile") in tiles() or not keys().isdisjoint(manifest.get("building_keys") or ())


MIGRATION = Rebuild("review-patches", 1, affected=affected)
