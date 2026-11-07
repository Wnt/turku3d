"""Private durable draft requests; a generator restart replays unfinished work."""

import json
import math
import os
import threading
import time
from pathlib import Path

from turku3d import config as C


class RequestJournal:
    def __init__(self, root: Path):
        self.path = root / ".requests.json"
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            rows = {}
        self.rows = {}
        self.dirty = False
        self.write_lock = threading.Lock()
        for tile, row in rows.items():
            try:
                parsed = C.VisualTile.parse(tile)
                if parsed.level == 0 and math.isfinite(float(row["at"])):
                    self.rows[tile] = row
            except (ValueError, TypeError, KeyError):
                continue

    def record(self, tile, distance, *, save=True):
        """``save=False`` under the scheduler lock: the caller calls save() after releasing it."""
        now = time.time()
        old = self.rows.get(tile, {})
        distance = distance if math.isfinite(distance) else 1e9
        if now - old.get("at", 0) > 2 or old.get("distance") != distance:
            self.rows[tile] = {"distance": distance, "at": now}
            self.dirty = True
            if save:
                self.save()

    def discard(self, tile, *, save=True):
        if self.rows.pop(tile, None) is not None:
            self.dirty = True
            if save:
                self.save()

    def save(self):
        """Write the journal if it changed. Writers never wait on each other: while one writes, another's change
        is written by it on its next round (city.lab Espoo 2026-11-07: every poll wrote this file under the
        scheduler lock on a host whose disk stalled for seconds)."""
        while self.dirty and self.write_lock.acquire(blocking=False):
            try:
                while self.dirty:
                    self.dirty = False
                    rows = dict(self.rows)
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
                    temporary.write_text(json.dumps(rows, sort_keys=True) + "\n")
                    temporary.replace(self.path)
            finally:
                self.write_lock.release()

    _save = save


def complete_tile(root: Path, tile: str, pattern) -> bool:
    """Only complete, safe prior outputs may serve during generator revalidation."""
    try:
        m = json.loads((root / tile / "manifest.json").read_text())
        visual = m["visual"][tile]
        cells = {c.id for c in C.VisualTile.parse(tile).collision_tiles()}
        if not m.get("coarse") or m.get("tile") != tile or set(m["collision"]) != cells:
            return False
        for entry in [visual, *m["collision"].values()]:
            if not pattern.fullmatch(entry["url"]):
                return False
            if (root / tile / entry["url"]).stat().st_size != entry["bytes"]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def request_tile(builder, tile: str, distance: float) -> dict:
    """Journal regeneration while immediately serving safe retained visuals."""
    with builder.lock:
        builder.journal.record(tile, distance, save=False)
    builder.journal.save()  # outside the scheduler lock
    # _request releases the condition while checking missing inputs. Do not retain
    # an outer RLock across that I/O or retained-fragment freshness validation.
    state = builder._request(tile, distance)
    from .serving import retained_fragment

    fragment = retained_fragment(builder, tile)
    result = state
    with builder.lock:
        if (
            state["state"] == "built"
            and tile not in builder.building
            and tile not in builder.upgrade_queue
        ):
            builder.journal.discard(tile, save=False)
        if fragment is not None:
            builder.servable = builder.servable | {tile}
            result = {"state": "built", "fragment": fragment}
            if fragment.get("stale"):
                result["stale"] = True
                if tile in builder.failed:
                    fragment["failure"] = {"state": "failed", "error": builder.failed[tile][0]}
    builder.journal.save()  # outside the scheduler lock
    return result
