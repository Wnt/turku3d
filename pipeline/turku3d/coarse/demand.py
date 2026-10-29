"""Short anonymous tile leases; clients send ordered tile ids, never motion or identity."""
import json
import os
import threading
import time

TTL = 12.0
MAX_CLIENTS = 128
MAX_TILES = 128


class Demand:
    def __init__(self):
        self.clients = {}
        self.managed = set()

    def expire(self):
        now = time.time()
        self.clients = {k: row for k, row in self.clients.items() if now - row[0] < TTL}

    def update(self, client, tiles, urgent, visible):
        self.expire()
        if client not in self.clients and len(self.clients) >= MAX_CLIENTS:
            return False
        self.clients[client] = (time.time(), list(tiles), set(urgent), set(visible))
        self.managed.update(tiles)
        return True

    def wants(self, client, tile):
        row = self.clients.get(client)
        return row is not None and time.time() - row[0] < TTL and tile in row[1]

    def rank(self, tile):
        ranks = [(0 if tile in urgent else 1 if tile in visible else 2, tiles.index(tile))
                 for at, tiles, urgent, visible in self.clients.values()
                 if time.time() - at < TTL and tile in tiles]
        return min(ranks) if ranks else None

    def admit(self, builder, tile):
        """A bounded queue must evict later work before rejecting an earlier crossing."""
        incoming = self.rank(tile)
        if incoming is None:
            return False
        waiting = [(self.rank(t) or (3, 0), t)
                   for t in set(builder.priority) | set(builder.pending)]
        if not waiting:
            return False
        rank, old = max(waiting)
        if rank <= incoming:
            return False
        builder.priority.pop(old, None)
        builder.pending.pop(old, None)
        builder.requested.pop(old, None)
        builder.journal.discard(old)
        return True

    def prune(self, builder):
        # A retiring producer finishes admitted work even after client leases expire.
        if getattr(builder, "draining", False):
            return
        self.expire()
        now = time.time()
        active = set(builder.priority) | set(builder.pending) | builder.upgrade_queue | set(builder.building)
        foreground = any(self.rank(t) is not None for t in active)
        for tile in active:
            rank = self.rank(tile)
            if getattr(builder, 'idle', None) is not None and tile in builder.idle.tiles and builder.idle.allowed():
                continue
            stale = (rank is None and (tile in self.managed or
                     now - builder.requested.get(tile, 0) > 12 or foreground))
            if not stale:
                continue
            builder.priority.pop(tile, None)
            builder.pending.pop(tile, None)
            builder.upgrade_queue.discard(tile)
            builder.upgrade_requested.pop(tile, None)
            builder.replay_until.pop(tile, None)
            builder.journal.discard(tile)
            if tile in builder.building and tile not in builder.cancelled:
                elapsed = now - builder.building[tile]
                recent = builder.full_recent if tile in builder.upgrading else builder.recent
                expected = sum(recent) / len(recent) if recent else (30 if tile in builder.upgrading else 8)
                # Keep a build estimated within its final two seconds; it may publish useful cached bytes.
                if hasattr(builder.run, 'deprioritize'):
                    builder.run.deprioritize(tile)
                if not max(1, expected - 2) <= elapsed <= expected + 2 and hasattr(builder.run, 'cancel'):
                    if builder.run.cancel(tile):
                        builder.cancelled.add(tile)
        self.managed.intersection_update(active | {t for _, ts, _, _ in self.clients.values() for t in ts})
        # Only reclaim a slot when no worker can start a collision-critical job.
        # Keep at least three quarters of the pool finishing its current work:
        # an existing first pass (or pending cancellation) already supplies that
        # capacity, so repeated demand heartbeats cannot cancel the survivors.
        urgent = any((rank := self.rank(t)) is not None and rank[0] == 0
                     for t in builder.priority)
        free = max(0, builder.workers - len(builder.building)) + len(builder.cancelled)
        first = len(set(builder.building) - builder.upgrading)
        allowance = max(1, builder.workers // 4) - first - len(builder.cancelled)
        if not urgent or free or allowance <= 0:
            return
        # Sacrifice the least invested upgrade, preserving work near completion.
        candidates = sorted(builder.upgrading - builder.cancelled,
                            key=lambda t: builder.building[t], reverse=True)
        for tile in candidates[:allowance]:
            if hasattr(builder.run, "cancel") and builder.run.cancel(tile):
                builder.cancelled.add(tile)
                if self.rank(tile) is not None:
                    builder.upgrade_queue.add(tile)


def write_snapshot(root, value):
    """Overlapping producer slots must never share the atomic-write temporary file."""
    path = root / ".scheduler.json"
    temporary = root / f".scheduler.{os.getpid()}.{threading.get_ident()}.tmp"
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(path)
