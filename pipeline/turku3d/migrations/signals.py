"""signals@1: traffic-signal posts at the kerb (``roads.signal_posts``) in the tile's ``roads.json``.

The rule moves each signal head's post from the carriageway to just past its nearest surveyed
edge (GIS:Katualueet carriageway parts, from the base's source version through the pipeline's own
loaders); ``signalPosts`` rows go into the street graph the client already loads, with the post's
ground height from the tile's own collision. Nothing else in the graph changes, so traffic, phases
and stop lines stay as they were. A tile without signal heads is unchanged; the non-public
``manifest.furniture.signals`` has the per-rule counts."""

from __future__ import annotations

import json

from turku3d import config as C

from .core import Migration, Product

WINDOW = 60.0  # m around the tile: carriageway polygons a head near the seam can reach


def transform(product: Product) -> None:
    from turku3d.coarse.floodlights import collision_ground
    from turku3d.coarse.sources import Sources
    from turku3d.roads import encode, signal_posts

    m = product.manifest
    entry = m.get("roads")
    if not entry:
        return
    graph = json.loads(product.read(entry["url"]))
    if graph.get("revision", 1) < 2 or not any(True for _ in signal_posts.heads(graph)):
        return
    tile = C.VisualTile.parse(product.tile)
    found, path = Sources().wfs_source("GIS:Katualueet", tile)
    road = signal_posts.load_carriageway(path if found else None, tile.bbox.buffered(WINDOW))
    ground = collision_ground(m.get("collision") or {}, product.read)
    rows, stats = signal_posts.place(graph, road, ground)
    graph["signalPosts"] = rows
    product.replace(entry, encode(graph))
    m.setdefault("furniture", {})["signals"] = dict(sorted(stats.items()))


MIGRATION = Migration("signals", 1, transform)
