"""slab-ends@1: spot rebuild after the slab end-wall rule (``turku3d.facade_blocks.end_walls``).

The short end walls of a rule-sampled slab block of flats dated 1940-1979 carry at most one column of
windows: an end wall 10 m wide or more is blank, a narrower one has one column in its middle, instead of
the sampled full window grid. Evidence (W2 full readings and street photos of such slabs): of 9 whole
ends 10 m wide or more 4 are blank, 1 has one column, 4 a few or full windows; of 6 narrower end pieces
5 have one column, 1 is blank; their long walls all have their windows; since 1980 7 of 12 end walls
are windowed (those keep the grid). Pilot: Heikkilankatu 2 at blocks-02 (1957, 8 storeys), blank in
the photo, drew a grid since shell-eaves@1 restored its walls.

@2 (pp-facades4): a wide end wall is no longer always blank. Each building draws its wide ends in the
evidence's proportions, 4 blank : 1 one column : 4 windowed (the sampled grid), by a stable pick on its
register id, so rebuilds never change it; narrower end pieces keep one column. The class is unchanged.

@3 (pp-facades5): an observed end wall is drawn as observed (``facade_blocks.draw_ends``): a reviewed
program wall on it draws itself, a W2 reading's end wall draws blank, one column or its grid by the window
columns read above its ground storey (``upper_columns`` in the reading rows), and the slab's unobserved
wide ends take the observed kind instead of the pick. New reviewed patch: Heikkilankatu 2's south-east end
wall, blank in the blocks-02 street photo (``textures/library/facade-programs.json``). Same class: every
observed slab is a slab-ends key.

The affected class is the tiles whose manifest lists a building of ``slab_ends_keys.json`` (written by
``python -m turku3d.facade_blocks ends`` from the city building table): every block of flats dated
1940-1979 with three or more register storeys and a slab footprint (at least 1.8 times as long as deep,
at most 16.5 m deep), a superset of the rule's class (descriptions, reading rows, OSM tags and which
walls are end walls are decided in the build). The rebuilt tiles are normal builds of this generator:
parity by construction. Every other tile keeps its payloads.
"""

import json
from functools import lru_cache
from pathlib import Path

from .core import Rebuild

KEYS = Path(__file__).with_name("slab_ends_keys.json")


@lru_cache(maxsize=1)
def keys() -> frozenset:
    return frozenset(json.loads(KEYS.read_text())["keys"])


def affected(manifest: dict) -> bool:
    return not keys().isdisjoint(manifest.get("building_keys") or ())


MIGRATION = Rebuild("slab-ends", 3, affected=affected)
