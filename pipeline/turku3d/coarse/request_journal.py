"""Private durable draft requests; a generator restart replays unfinished work."""

import json
import math
import os
import time
from pathlib import Path

from turku3d import config as C

MAX_AGE = 600


class RequestJournal:
    def __init__(self, root: Path):
        self.path = root / ".requests.json"
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            rows = {}
        self.rows = {}
        for tile, row in rows.items():
            try:
                parsed = C.VisualTile.parse(tile)
                if parsed.level == 0 and time.time() - float(row["at"]) < MAX_AGE:
                    self.rows[tile] = row
            except (ValueError, TypeError, KeyError):
                continue

    def record(self, tile, distance):
        now = time.time()
        old = self.rows.get(tile, {})
        distance = distance if math.isfinite(distance) else 1e9
        if now - old.get("at", 0) > 2 or old.get("distance") != distance:
            self.rows[tile] = {"distance": distance, "at": now}
            self._save()

    def discard(self, tile):
        if self.rows.pop(tile, None) is not None:
            self._save()

    def _save(self):
        self.rows = {t: row for t, row in self.rows.items() if time.time() - row["at"] < MAX_AGE}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(self.rows, sort_keys=True) + "\n")
        temporary.replace(self.path)


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
