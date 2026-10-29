"""Serve complete visual/collision generations while their replacements rebuild."""

import json

from turku3d import config as C

PUBLIC_FIELDS = (
    "coarse", "tile", "visual", "collision", "roads", "pass_kind", "vehicles",
    "vehicle_models", "stale", "generation", "failure",
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
        return all(FILE.fullmatch(e["url"]) and (root / tile / e["url"]).stat().st_size == e["bytes"]
                   for e in entries)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def retained_fragment(builder, tile):
    """Keep last-good full visuals with their own collision until a full pair replaces them.

    Fresh first-pass collision belongs to its basic visual, never to retained full
    geometry. A producer/input change schedules regeneration without removing support.
    """
    manifest = _manifest(builder, tile)
    if (manifest and manifest.get("pass_kind") == "full" and builder.built(tile)
            and complete(builder.root, tile, manifest)):
        return {**manifest, "generation": manifest.get("code")}
    candidates = []
    for name in ("manifest.json", "last-full.json", "collision.json", "first.json"):
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
