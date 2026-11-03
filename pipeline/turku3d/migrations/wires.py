"""wires@2: overhead wires and the street lamps hung on them, from the survey lidar (``coarse.wires``).

A pure data migration: the tile's own ``pistepilvi`` returns give span wires between facades with
their hung lamps, and utility lines between poles. The list rides in the existing client-visible
``lights`` field (``lights.wires``), so neither the farm's public keys nor any payload changes.
A tile without survey on disk, or without wires, is unchanged."""

from turku3d import config as C
from turku3d.coarse import wires

from .core import Migration, Product

EMPTY_LIGHTS = {"format": "turku3d-lights", "version": 1, "count": 0, "posts": 0, "street": 0, "data": ""}


def transform(product: Product) -> None:
    record = wires.tile_wires(C.VisualTile.parse(product.tile))
    if record is None:
        return
    m = product.manifest
    m["lights"] = {**(m.get("lights") or EMPTY_LIGHTS), "wires": record}


MIGRATION = Migration("wires", 2, transform)
