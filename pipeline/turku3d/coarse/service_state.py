"""Cache freshness and in-memory scheduler reporting; no build admission."""

import json

from turku3d.facade_kit import parameter_hash
from turku3d.landmarks.descriptions import manifest_current

from .build_health import error_kind
from .sources import NoCoverage


def unavailable_reply(builder, tile):
    error = builder.failed.get(tile, (None, 0))[0]
    if (
        error
        and error_kind(error) == "NoCoverage"
        and builder.failure_codes.get(tile) == builder.failure_key(tile)
    ):
        builder.priority.pop(tile, None)
        builder.upgrade_queue.discard(tile)
        builder.journal.discard(tile)
        return dict(state="failed", error=error)
    return None


def tile_current(self, tile: str, code: str | None = None) -> bool:
    """``code``: the producer hash when the caller checks many tiles (hashed once, not per tile)."""
    path = self.manifest(tile)
    if not path.exists():
        return False
    try:
        m = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    self.code_seen = code = code or self.code()
    if m.get("code") != code:
        return False
    if "building_keys" in m and m.get("facade_params") != parameter_hash(m["building_keys"]):
        return False
    if not manifest_current(m):
        return False
    try:
        key = (
            (self.structural_input_key or self.input_key)
            if m.get("pass_kind") == "first"
            else self.input_key
        )
        now = key(tile)
    except NoCoverage:
        return True
    return now is None or m.get("inputs") == now


def failed_identity(self, tile):
    try:
        return self.code(), self.input_key(tile)
    except NoCoverage:
        return self.code(), None


def snapshot(self, limit) -> dict:
    """The builders' in-memory state, small: no manifest, input or source file is read, so it
    answers in milliseconds however loaded the host and however many drafts are on disk."""
    with self.lock:
        order = self.order()
        eta = self.eta()
        shown = [*sorted(self.building), *order[:limit]]
        return {
            "state": "building" if self.building else "idle",
            "building": sorted(self.building),
            "upgrading": sorted(self.upgrading),
            "foreground": sum(
                self.demand.rank(t) is not None
                for t in set(order) | set(self.building) | set(self.pending)
            ),
            "queued": len(order),
            "queue": order[:limit],
            "eta": {tile: eta[tile] for tile in shown},
            "version": self.version,
            "built": self.built_count,
            "failure_count": self.failure_count,
            "failed": {tile: error for tile, (error, _) in list(self.failed.items())[-limit:]},
            "pending": {tile: units for tile, (units, _) in list(self.pending.items())[-limit:]},
            "timings": dict(self.timings),
            "code": self.code_seen,
            "draining": self.draining,
            "prebuild": self.idle.status() if self.idle else None,
        }
