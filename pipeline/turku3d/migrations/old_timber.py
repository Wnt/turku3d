"""old-timber@2: spot rebuild after the old-wooden-house rules (``turku3d.facade_timber``,
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


MIGRATION = Rebuild("old-timber", 2, affected=affected)
