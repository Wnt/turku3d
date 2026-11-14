"""shell-eaves-outer@1: shell-eaves@1's spot rebuild for the tiles beyond rings 0-3.

The same fix as ``shell_eaves`` (exposed wall pieces take their shell ring's eave; a tower's register
storeys span the floor grid), for the tiles shell-eaves@1 does not list: every farm base tile with
buildings whose centre lies more than 4 km from Kauppatori (2,367 tiles, ``shell_eaves_outer_tiles.json``,
nearest first, from the base manifests' building keys). pp-shells' survey of the rings 4+ tiles the Mac
had raw sources for: 160 of 199 tiles with buildings change (782 buildings had 0.1 m walls). A step of
its own name, so the rings 0-3 tiles are not rebuilt again and the farm can feed these after them. No new
code: the rebuilt tiles are normal builds of this generator, parity by construction.
"""

import json
from functools import lru_cache
from pathlib import Path

from .core import Rebuild

TILES = Path(__file__).with_name("shell_eaves_outer_tiles.json")


@lru_cache(maxsize=1)
def tiles() -> frozenset:
    return frozenset(json.loads(TILES.read_text())["tiles"])


def affected(manifest: dict) -> bool:
    return manifest.get("tile") in tiles()


MIGRATION = Rebuild("shell-eaves-outer", 1, affected=affected)
