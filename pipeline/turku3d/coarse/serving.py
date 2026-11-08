"""Serve complete visual/collision generations while their replacements rebuild."""

import json
import re
import threading
import time

from turku3d import config as C

PUBLIC_FIELDS = (
    "coarse", "tile", "visual", "collision", "roads", "pass_kind", "vehicles", "lights",
    "vehicle_models", "buildingPlacements", "stale", "generation", "failure",
)


def public_fragment(value):
    result = {k: value[k] for k in PUBLIC_FIELDS if k in value}
    if "failure" in result:
        result["failure"] = {"state": "failed"}
    return result


def complete(root, tile, value):
    from .service import FILE

    try:
        if not value.get("coarse") or value.get("tile") != tile:
            return False
        if set(value["collision"]) != {c.id for c in C.VisualTile.parse(tile).collision_tiles()}:
            return False
        entries = [value["visual"][tile], *value["collision"].values()]
        if value.get("roads"):
            entries.append(value["roads"])
        for model in value.get("buildingPlacements", {}).get("models", []):
            name = model["id"] + ".glb"
            if model["uri"] != "../shared/" + name or not re.fullmatch(r"[a-f0-9]{64}\.glb", name):
                return False
            if (root / "shared" / name).stat().st_size != model["bytes"]:
                return False
        return all(FILE.fullmatch(e["url"]) and (root / tile / e["url"]).stat().st_size == e["bytes"]
                   for e in entries)
    except (OSError, ValueError, KeyError, TypeError):
        return False


FRAGMENT_FRESH_S = 5.0  # a tile's served fragment is reused this long before it is read from disk again


def fragment_key(builder, tile, code=None):
    """The producer code and the signatures of the tile's candidate files its served fragment was read from."""
    return code or builder.code(), tuple(_signature(builder.root / tile / name) for name in CANDIDATES)


def fragment_current(builder, tile, code=None):
    """Whether the tile's kept fragment was read from its current files under the current code."""
    hit = getattr(builder, "fragment_memo", {}).get(tile)
    return hit is not None and hit[0][1:] == fragment_key(builder, tile, code)


def retained_fragment(builder, tile):
    """The tile's served fragment (see _retained_fragment), kept in memory per tile: every manifest and collision
    poll asks for it, and reading it parses its manifests and stats every payload and shared model, which took up to
    2-3 s on a loaded host (city.lab Espoo 2026-11-07, load 45-60). A kept fragment answers at once while the producer
    code and the tile's candidate files are unchanged. After FRAGMENT_FRESH_S, or once any build has finished
    (builder.version: a shared model it needs may have landed), one re-read runs behind the answer. A finished build
    used to make every tile's next poll re-read it in the foreground: 4-16 s per tile behind each finished build on a
    loaded host (city.lab Amsterdam, 2026-11-08)."""
    memo = getattr(builder, "fragment_memo", None)
    if memo is None:
        return _retained_fragment(builder, tile)
    key = (builder.version, *fragment_key(builder, tile))
    hit = memo.get(tile)
    if hit is not None and hit[0][1:] == key[1:]:
        stale = hit[0][0] != key[0] or time.monotonic() - hit[2] >= FRAGMENT_FRESH_S
        if stale and tile not in builder.fragment_refreshing:
            builder.fragment_refreshing.add(tile)
            kept = hit[0]

            def refresh():
                try:
                    value = _retained_fragment(builder, tile)
                    if memo.get(tile, (None,))[0] == kept:  # nothing replaced the entry while this read
                        memo[tile] = (key, value, time.monotonic())
                except Exception:  # noqa: BLE001 - the next poll reads again
                    memo.pop(tile, None)
                finally:
                    builder.fragment_refreshing.discard(tile)

            threading.Thread(target=refresh, name=f"coarse-fragment-{tile}", daemon=True).start()
        return None if hit[1] is None else dict(hit[1])
    value = _retained_fragment(builder, tile)
    memo[tile] = (key, value, time.monotonic())
    return None if value is None else dict(value)


CANDIDATES = ("manifest.json", "last-full.json", "collision.json", "first.json")


def _signature(path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _retained_fragment(builder, tile):
    """Keep last-good full visuals with their own collision until a full pair replaces them.

    Fresh first-pass collision belongs to its basic visual, never to retained full
    geometry. A producer/input change schedules regeneration without removing support.
    """
    manifest = _manifest(builder, tile)
    if (manifest and manifest.get("pass_kind") == "full" and builder.built(tile)
            and complete(builder.root, tile, manifest)):
        return {**manifest, "generation": manifest.get("code")}
    candidates = []
    for name in CANDIDATES:
        path = builder.root / tile / name
        try:
            value = json.loads(path.read_text())
            if complete(builder.root, tile, value):
                candidates.append((value.get("pass_kind") == "full", value.get("built", ""),
                                   path.stat().st_mtime_ns, value))
        except (OSError, ValueError, TypeError):
            continue
    candidates.sort(key=lambda row: row[:3], reverse=True)
    if not candidates:
        return None
    value = candidates[0][-1]
    result = {**value, "generation": value.get("code")}
    if not builder.built(tile) or value.get("code") != builder.code():
        result["stale"] = True
    return result


def _manifest(builder, tile):
    try:
        return json.loads(builder.manifest(tile).read_text())
    except (OSError, ValueError):
        return None
