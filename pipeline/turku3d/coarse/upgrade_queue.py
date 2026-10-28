"""Full upgrades use spare workers and remain wanted after the structural first pass is served."""

import json
import time

from turku3d import config as C


def upgrade_order(builder, tile):
    requested = builder.upgrade_requested.get(tile, 0)
    t = C.VisualTile.parse(tile)
    x, _, z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
    distance = (t.origin[0] + t.size / 2 - x) ** 2 + (t.origin[2] + t.size / 2 - z) ** 2
    return (0 if requested else 1, -requested, distance, tile)


def next_upgrade(builder):
    if builder.draining:
        return None
    for tile in sorted(builder.upgrade_queue, key=lambda t: upgrade_order(builder, t)):
        if tile in builder.building:
            continue
        if tile in builder.failed and time.time() - builder.failed[tile][1] < 120:
            continue
        builder.upgrade_queue.discard(tile)
        builder.upgrading.add(tile)
        return tile, 0.0
    return None


def next_job(builder, upgrade_only=False):
    if not upgrade_only:
        first = builder._next()
        if first is not None:
            tile, _ = first
            # Retain a textured previous generation until its complete successor is ready.
            if builder.two_pass and builder.cached(tile):
                manifest = json.loads(builder.manifest(tile).read_text())
                if manifest.get("pass_kind") == "full":
                    builder.upgrading.add(tile)
                    return first, True
            return first, False
    upgrade = next_upgrade(builder) if builder.two_pass or upgrade_only else None
    return (upgrade, True) if upgrade is not None else None
