"""Full upgrades use spare workers and remain wanted after the structural first pass is served."""

import time

from turku3d import config as C


def upgrade_order(builder, tile):
    if builder.idle is not None and tile in builder.idle.tiles:
        return (2, tile)
    lease = builder.demand.rank(tile)
    if lease is not None:
        return (-1, *lease, tile)
    requested = builder.upgrade_requested.get(tile, 0)
    t = C.VisualTile.parse(tile)
    x, _, z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
    distance = (t.origin[0] + t.size / 2 - x) ** 2 + (t.origin[2] + t.size / 2 - z) ** 2
    return (0 if requested else 1, -requested, distance, tile)


def next_upgrade(builder, *, before=None):
    if builder.draining:
        return None
    # Reserve crossing capacity even between leases: a returning client must
    # never need to kill a committed full pass to obtain its first-pass worker.
    # Explicitly preemptible idle jobs may still fill the entire pool.
    idle_tiles = builder.idle.tiles if builder.idle is not None else set()
    firsts = (set(builder.priority) | (set(builder.building) - builder.upgrading)) - builder.cancelled
    urgent_first = min(
        (r for first in firsts if (r := builder.demand.rank(first)) is not None and r[0] == 0),
        default=None,
    )
    for tile in sorted(builder.upgrade_queue, key=lambda t: upgrade_order(builder, t)):
        limit = max(1, builder.workers - 1)
        lease = builder.demand.rank(tile)
        if before is not None and (lease is None or lease >= before):
            continue
        # A nearer crossing may still be building its first pass. Leave room
        # for its upgrade instead of committing every upgrade slot farther ahead.
        if builder.workers > 1 and urgent_first is not None and (lease is None or urgent_first < lease):
            limit -= 1
        if tile not in idle_tiles and len(builder.upgrading) >= limit:
            continue
        if tile in builder.building:
            continue
        if tile in builder.failed and time.time() - builder.failed[tile][1] < 120:
            continue
        builder.upgrade_queue.discard(tile)
        builder.upgrading.add(tile)
        return tile, 0.0
    return None


def next_job(builder, upgrade_only=False):
    if builder.idle is not None:
        with builder.lock:
            builder.idle.preempt()
    if not upgrade_only:
        order = builder.order()
        imminent = bool(order and builder.job_rank(order[0], builder.priority[order[0]])[0] == 0)
        # The urgent lease includes a long swept corridor. Finishing a nearer
        # crossing must not wait for every distant first pass in that corridor.
        # Equal-ranked crossings from another player still get their first pass,
        # and next_upgrade retains the worker reserved for new crossings.
        first_lease = builder.demand.rank(order[0]) if imminent else None
        if first_lease is not None:
            upgrade = next_upgrade(builder, before=first_lease)
            if upgrade is not None:
                return upgrade, True
        visible_upgrade = any(
            (r := builder.demand.rank(t)) is not None and r[0] <= 1 for t in builder.upgrade_queue
        )
        if not imminent and visible_upgrade:
            upgrade = next_upgrade(builder)
            if upgrade is not None:
                return upgrade, True
        first = builder._next()
        if first is not None:
            return first, False
    upgrade = next_upgrade(builder) if builder.two_pass or upgrade_only else None
    return (upgrade, True) if upgrade is not None else None
