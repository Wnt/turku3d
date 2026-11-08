"""span-lamps@1: lamps where a lighting span wire the survey saw without one crosses a lit street
(``coarse.span_lamps``).

A pure data migration after ``wires``: it reads the tile's ``lights.wires`` record and register
lights, the lit streets from the tile's OSM source and the ground of its own collision, and appends the inferred lamps to
``lights.wires.lamps`` (extending a wire to the lamps past its surveyed ends), so the client hangs,
lights and pools them like the surveyed ones. A tile without such a span wire is unchanged."""

from turku3d import config as C
from turku3d.coarse import span_lamps
from turku3d.props.lights import decode

from .core import Migration, Product


def transform(product: Product) -> None:
    from turku3d.coarse.sources import Sources

    m = product.manifest
    lights = m.get("lights") or {}
    record = lights.get("wires")
    if not record or not record.get("wires"):
        return
    tile = C.VisualTile.parse(product.tile)
    osm = Sources().osm_source(tile)
    if osm is None:
        return
    from turku3d.coarse.floodlights import collision_ground

    hung = [(light["x"], light["z"]) for light in decode(lights)[0] if light["category"] == "hung"]
    ground = collision_ground(m.get("collision") or {}, product.read)
    updated, _ = span_lamps.infer(record, span_lamps.streets(osm, tile.bbox), hung, ground)
    if updated is not None:
        m["lights"] = {**lights, "wires": updated}


MIGRATION = Migration("span-lamps", 1, transform)
