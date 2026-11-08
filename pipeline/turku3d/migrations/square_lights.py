"""square-lights@1: mast lighting of open squares that no mapped lamp lights (``coarse.square_lights``).

A pure data migration: the squares and the lamps round them come from the tile's OSM source, ground
heights from the tile's own collision. Each dark square joins the night floodlights in the existing
client-visible ``lights.floodlights`` record as a ``square`` venue with its masts, so the client lights
it with the area light and draws its masts like a sports venue's layout masts. A tile without a dark
square is unchanged."""

from turku3d import config as C
from turku3d.coarse import floodlights, square_lights

from .core import Migration, Product
from .floodlights import EMPTY_LIGHTS


def transform(product: Product) -> None:
    from turku3d.coarse.sources import Sources

    m = product.manifest
    tile = C.VisualTile.parse(product.tile)
    osm = Sources().osm_source(tile)
    if osm is None:
        return
    ground = floodlights.collision_ground(m.get("collision") or {}, product.read)
    venues, masts = square_lights.tile_square_lights(tile, osm, ground)
    if not venues and not masts:
        return
    lights = m.get("lights") or EMPTY_LIGHTS
    record = lights.get("floodlights") or {"version": floodlights.FORMAT_VERSION, "venues": [], "masts": []}
    m["lights"] = {**lights, "floodlights": {
        **record,
        "venues": sorted([*record["venues"], *venues], key=lambda v: v["id"]),
        "masts": sorted([*record["masts"], *masts], key=lambda v: v["id"]),
    }}


MIGRATION = Migration("square-lights", 1, transform)
