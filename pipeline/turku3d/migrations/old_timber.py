"""old-timber@3: spot rebuild after the old-wooden-house rules (``turku3d.facade_timber``,
``turku3d.coarse.register_timber``).

Rule-sampled timber houses dated before 1940 stand on a projecting granite or red-brick plinth,
keep the register's storeys at a believable height (the wall above the window heads is a boarded
knee wall) and get small framed six-pane or T-windows instead of one storey stretched over the
eaves wall. The register's pre-1940 facade material now reaches the whole city: "puu" is timber,
and low residential houses of "muu materiaali" are inferred timber, both below AI descriptions.

@2 (pp-wood2): the wall above the storeys is no longer blank (eaves windows where the register
counts a storey in the roof or the knee wall is tall, the roof storey's windows and an attic light in
the gables), the plinth is the area's (granite or brick in the town of the centre milieu typing,
grey concrete under the 1920s-30s houses outside it), sashes stand upright, and window jambs drop
their hidden level returns. The class is unchanged; the version moves so that a derive inheriting
@1's builds (``--rebuilds-from``) cannot keep the old layout.

@3 (pp-facades3): the row under the eaves is decided on the band over the window heads before the
plinth lift, not after it: one-storey houses on 6.4-6.6 m walls whose plinth the layout lifted to
1.5-1.6 m kept a 2.15-2.35 m blank band (8 of pp-wood2's 115 evidence houses). The class is unchanged.

The affected class is the tiles whose manifest lists a building of ``old_timber_keys.json``
(written by ``python -m turku3d.coarse.register_timber build``): every dated pre-1940 building the
register calls wood or the inference makes wood, every one OSM tags wood, and the reviewed ones
dated before 1940 (a superset: the description and reading precedence needs the build). The
rebuilt tiles are normal builds of this generator: parity by construction. Every other tile keeps
its payloads.
"""

import json
from functools import lru_cache
from pathlib import Path

from .core import Rebuild

KEYS = Path(__file__).with_name("old_timber_keys.json")


@lru_cache(maxsize=1)
def keys() -> frozenset:
    return frozenset(json.loads(KEYS.read_text())["keys"])


def affected(manifest: dict) -> bool:
    return not keys().isdisjoint(manifest.get("building_keys") or ())


MIGRATION = Rebuild("old-timber", 3, affected=affected)
