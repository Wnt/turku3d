"""block-windows@1: spot rebuild after "a block with a surveyed wall keeps one window type".

block-balconies@1 gave 1980+ blocks of flats single-light windows on every wall except a reviewed
(surveyed) one, so a block with a surveyed wall drew two window types side by side (Uudenmaankatu 19
and Itainen Pitkakatu 24 at uudenmaa-01; Fable 2026-11-14 rated the view 5 -> 4). Such a block now
keeps its windows on every wall; balconies still follow the rule (``turku3d.facade_blocks``).

The affected class is the tiles whose manifest lists a building of ``block_windows_keys.json``: the
blocks of ``block_balconies_keys.json`` dated 1980 or later (or undated) that have a reviewed program in
``textures/library/facade-programs.json`` (51 buildings), a superset of the changed ones. The rebuilt
tiles are normal builds of this generator: parity by construction.
"""

import json
from functools import lru_cache
from pathlib import Path

from .core import Rebuild

KEYS = Path(__file__).with_name("block_windows_keys.json")


@lru_cache(maxsize=1)
def keys() -> frozenset:
    return frozenset(json.loads(KEYS.read_text())["keys"])


def affected(manifest: dict) -> bool:
    return not keys().isdisjoint(manifest.get("building_keys") or ())


MIGRATION = Rebuild("block-windows", 1, affected=affected)
