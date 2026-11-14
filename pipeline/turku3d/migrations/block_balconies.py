"""block-balconies@1: spot rebuild after the block-of-flats rule (``turku3d.facade_blocks``).

Rule-sampled blocks of flats (register residential class, three or more register storeys, dated
1940 or later) carry balcony stacks on every tall wall facing within 45 degrees of their sunniest
one, by period (cantilevers with a sheet front 1940-59, loggias or cantilevers with concrete fronts
1960-79, glazed balconies since 1980), instead of the archetype's random painted rails on every
wall; blocks since 1980 get single-light windows 1.4 m wide. Below AI descriptions, reading rows
and reviewed walls. Evidence: W2's 36 full readings of blocks, town and suburbs (w2-rules 6c0a8090).

The affected class is the tiles whose manifest lists a building of ``block_balconies_keys.json``
(written by ``python -m turku3d.facade_blocks build`` from the city building table): every dated
block of flats from 1940 on with three or more register storeys, a superset of the rule's class
(descriptions, reading rows and OSM tags are decided in the build). The rebuilt tiles are normal
builds of this generator: parity by construction. Every other tile keeps its payloads.
"""

import json
from functools import lru_cache
from pathlib import Path

from .core import Rebuild

KEYS = Path(__file__).with_name("block_balconies_keys.json")


@lru_cache(maxsize=1)
def keys() -> frozenset:
    return frozenset(json.loads(KEYS.read_text())["keys"])


def affected(manifest: dict) -> bool:
    return not keys().isdisjoint(manifest.get("building_keys") or ())


MIGRATION = Rebuild("block-balconies", 1, affected=affected)
