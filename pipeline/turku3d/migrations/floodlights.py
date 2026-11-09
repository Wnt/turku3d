"""floodlights@1: night floodlights of lit sports venues (``coarse.floodlights``) in ``lights``.

A pure data migration: the venues and their masts come from the tile's OSM source, mast heights
from the harbour stage's drawn lattice masts (``boats.harbour``), ground heights from the tile's
own collision. The list rides in the existing client-visible ``lights`` field
(``lights.floodlights``), so neither the farm's public keys nor any payload changes."""

from turku3d import config as C
from turku3d.coarse import floodlights

from .core import Migration, Product

EMPTY_LIGHTS = {"format": "turku3d-lights", "version": 1, "count": 0, "posts": 0, "street": 0, "data": ""}


def transform(product: Product) -> None:
    from turku3d.coarse.sources import current_sources

    m = product.manifest
    tile = C.VisualTile.parse(product.tile)
    osm = current_sources().osm_source(tile)
    if osm is None:
        return
    harbour = {h["id"]: float(h["height_m"]) for h in (m.get("boats") or {}).get("harbour", ())
               if h.get("kind") in ("mast", "tower") and h.get("height_m")}
    ground = floodlights.collision_ground(m.get("collision") or {}, product.read)
    record = floodlights.tile_floodlights(tile, osm, ground, harbour)
    if record is None:
        return
    m["lights"] = {**(m.get("lights") or EMPTY_LIGHTS), "floodlights": record}


MIGRATION = Migration("floodlights", 1, transform)
