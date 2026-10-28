"""Short anonymous tile leases; clients send ordered tile ids, never motion or identity."""
import time
import json
import os
import threading

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

    def prune(self, builder):
        self.expire()
        now = time.time()
        active = set(builder.priority) | set(builder.pending) | builder.upgrade_queue | set(builder.building)
        foreground = any(self.rank(t) is not None for t in active)
        for tile in active:
            rank = self.rank(tile)
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


def write_snapshot(root, value):
    """Overlapping producer slots must never share the atomic-write temporary file."""
    path = root / ".scheduler.json"
    temporary = root / f".scheduler.{os.getpid()}.{threading.get_ident()}.tmp"
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(path)
