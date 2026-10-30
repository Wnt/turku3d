"""Deliver complete cached tiles without waiting for source freshness checks."""

import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from turku3d.coarse.serving import complete, public_fragment


class ReadyFragments:
    """Publication changes invalidate immediately; source checks run in a bounded background pool.

    An unvalidated retained pair is explicitly stale, exactly as during regeneration.
    A missing/incomplete pair stays on the ordinary build/admission path.
    """

    def __init__(self, root, key, interval=5):
        self.root, self.key, self.interval = root, key, interval
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.pending = set()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cached-tile-freshness")

    def get(self, tile, refresh):
        key = self.key(tile)
        with self.lock:
            entry = self.entries.get(tile)
            if entry is not None and entry["key"] != key:
                del self.entries[tile]
                entry = None
        if entry is None:
            fragment = None
            for name in ("manifest.json", "last-full.json"):
                try:
                    value = json.loads((self.root / tile / name).read_text())
                    if value.get("pass_kind") == "full" and complete(self.root, tile, value):
                        fragment = public_fragment({**value, "generation": value.get("code"), "stale": True})
                        break
                except (OSError, ValueError, TypeError):
                    continue
            if fragment is None:
                return None
            # A publisher may replace metadata while files are being validated.
            if self.key(tile) != key:
                return None
            entry = {"key": key, "fragment": fragment, "next": 0}
            with self.lock:
                self.entries[tile] = entry
                while len(self.entries) > 256:
                    self.entries.popitem(last=False)
        with self.lock:
            if self.entries.get(tile) is entry:
                self.entries.move_to_end(tile)
            if time.monotonic() >= entry["next"] and tile not in self.pending and len(self.pending) < 32:
                self.pending.add(tile)
                entry["next"] = time.monotonic() + self.interval
                self.pool.submit(self._refresh, tile, entry, refresh)
            return entry["fragment"]

    def _refresh(self, tile, entry, refresh):
        try:
            status, fragment = refresh()
            if status == 200 and fragment.get("pass_kind") == "full" and self.key(tile) == entry["key"]:
                with self.lock:
                    if self.entries.get(tile) is entry:
                        entry["fragment"] = fragment
        except Exception as error:
            print(json.dumps({"cached_tile_refresh_error": type(error).__name__}), flush=True)
        finally:
            with self.lock:
                self.pending.discard(tile)
                entry["next"] = time.monotonic() + self.interval

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
