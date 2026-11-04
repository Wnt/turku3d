"""landmark-windows@1: spot rebuild after the landmark window rule.

``landmarks.procedural.window_rows`` reads an empty ``rows_m`` as unmeasured (rows per storey) and
``landmarks.appearance.line_mesh`` lets survey wall pieces share their facade line's openings. Only
landmark walls change, and a landmark is built in the tile that owns it, so the tiles whose
manifest lists a building with a landmark description are the affected class; every other tile
keeps its payloads. The rebuilt tiles are normal builds of this generator: parity by construction.
"""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    from turku3d.landmarks.descriptions import described_refs

    return described_refs(manifest.get("landmark_buildings") or ())


MIGRATION = Rebuild("landmark-windows", 1, affected=affected)
