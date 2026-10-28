"""Full upgrades use spare workers and remain wanted after the structural first pass is served."""

import time

from turku3d import config as C


def upgrade_order(builder, tile):
    lease = builder.demand.rank(tile)
    if lease is not None:
        return (-1, *lease, tile)
    requested = builder.upgrade_requested.get(tile, 0)
    t = C.VisualTile.parse(tile)
    x, _, z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
    distance = (t.origin[0] + t.size / 2 - x) ** 2 + (t.origin[2] + t.size / 2 - z) ** 2
    return (0 if requested else 1, -requested, distance, tile)


def next_upgrade(builder):
    if builder.draining:
        return None
    # Leave one of the fixed worker slots available for a newly predicted crossing.
    if builder.demand.clients and len(builder.upgrading) >= max(1, builder.workers - 1):
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
        order = builder.order()
        imminent = bool(order and builder.job_rank(order[0], builder.priority[order[0]])[0] == 0)
        visible_upgrade = any((r := builder.demand.rank(t)) is not None and r[0] <= 1
                              for t in builder.upgrade_queue)
        if not imminent and visible_upgrade:
            upgrade = next_upgrade(builder)
            if upgrade is not None:
                return upgrade, True
        first = builder._next()
        if first is not None:
            return first, False
    upgrade = next_upgrade(builder) if builder.two_pass or upgrade_only else None
    return (upgrade, True) if upgrade is not None else None
