"""facade-readings@3: spot rebuild of the tiles holding a building with a converted W2 reading.

W2's vision readings (wall material and colour, storeys, bays and window sizes, ground storey,
plinth, roof line, bands, pilasters, balconies) of buildings that proven neither describes nor
reviews ship as facade description rows in ``textures/library/facade-readings``, written offline
by ``turku3d.facade_readings_convert`` and looked up through ``turku3d.facade_readings``.
``facade_assets.description_row`` consults them only after every proven source, so proven's
descriptions and reviewed programs keep priority. The affected class
is every tile whose manifest lists such a building. The rebuilt tiles are normal builds of this
generator: parity by construction. A new set of rows (more readings) is a new version of this
step, since the predicate's code hash does not cover the row index.
"""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    from turku3d.facade_assets import aliases
    from turku3d.facade_readings import keys

    read = keys()
    return any(a in read for key in manifest.get("building_keys") or () for a in aliases(key))


# @2: trim from the read pilasters and cornices, windows sized to the drawn pitch (2026-11-14).
# @3: windows at the mean size of the read rows, not the most frequent light (2026-11-14).
MIGRATION = Rebuild("facade-readings", 3, affected=affected)
