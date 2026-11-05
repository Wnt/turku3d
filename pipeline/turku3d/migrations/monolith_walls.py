"""monolith-walls@3: spot rebuild after the public-monolith wall rule (``turku3d.facade_monolith``).

A cultural building with one photographed blank wall used to paint every unphotographed wall as
a blank volume, so whole theatres, museums and libraries rendered windowless. An unphotographed
wall now inherits the type of the photographed wall facing the same side (else its nearest
photographed neighbour): blank volume, curtain wall or described windows; blank volumes get a
glazed street storey where photographs or an OSM entrance node show one, and their granite base
course is painted in its own stone cell. Donors on the wall's own building part (same top
height and footprint part) come first. (@1, the ordinary-class fallback, and @2, which let a
low wing inherit the glass box beside it, never shipped.)
Only buildings with a stored blank-wall photograph can enter the class, so the tiles whose
manifest lists such a building are the affected class (a superset: the use/year/footprint gate
needs the build's attributes). The rebuilt tiles are normal builds of this generator: parity by
construction. Every other tile keeps its payloads.
"""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    from turku3d import facade_monolith
    from turku3d.facade_programs import measured

    return any(facade_monolith.evidence(key, measured(key))["blank"]
               for key in manifest.get("building_keys") or ())


MIGRATION = Rebuild("monolith-walls", 3, affected=affected)
