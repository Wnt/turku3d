"""span-lamps@2: lamps where a lighting span wire the survey saw without one crosses a lit street
(``coarse.span_lamps``).

A data migration after ``wires``: interior lamps use the tile's wire record; border lamps use
content-addressed common survey windows so the tile holding the street crossing owns the lamp,
even when it owns neither wire fragment. No neighbouring product, handshake or full build is needed.
The existing ``lights.wires`` representation includes lamps on another tile's wire (index -1)."""

from turku3d import config as C
from turku3d.coarse import span_lamps, span_lamp_seams
from turku3d.props.lights import decode

from .core import Migration, Product


def transform(product: Product) -> None:
    from turku3d.coarse.sources import current_sources

    m = product.manifest
    lights = m.get("lights") or {}
    record = lights.get("wires")
    tile = C.VisualTile.parse(product.tile)
    osm = current_sources().osm_source(tile)
    if osm is None:
        return
    from turku3d.coarse.floodlights import collision_ground

    hung = [(light["x"], light["z"]) for light in decode(lights)[0] if light["category"] == "hung"]
    ground = collision_ground(m.get("collision") or {}, product.read)
    updated = span_lamp_seams.infer(tile, record, span_lamps.streets(osm, tile.bbox), hung, ground)
    if updated is not None:
        from .wires import EMPTY_LIGHTS

        m["lights"] = {**(lights or EMPTY_LIGHTS), "wires": updated}


MIGRATION = Migration("span-lamps", 2, transform)
