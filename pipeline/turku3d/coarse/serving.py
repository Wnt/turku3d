"""Serve retained visuals only with collision proven current for this tile."""

import json

from turku3d import config as C
from .collision import current as current_collision

PUBLIC_FIELDS = (
    "coarse", "tile", "visual", "collision", "roads", "pass_kind", "vehicles",
    "vehicle_models", "stale", "collision_source", "failure",
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
    """Newest complete full visuals + current collision; never revive obsolete physics.

    The structural identity includes producer and input content. Unknown identity is
    not permission to serve old collision, even when regeneration has failed.
    """
    manifest = _manifest(builder, tile)
    if (manifest and manifest.get("pass_kind") == "full" and builder.built(tile)
            and complete(builder.root, tile, manifest)):
        return manifest
    candidates = []
    for name in ("manifest.json", "last-full.json"):
        path = builder.root / tile / name
        try:
            value = json.loads(path.read_text())
            if value.get("pass_kind") == "full" and complete(builder.root, tile, value):
                candidates.append((value.get("built", ""), path.stat().st_mtime_ns, value))
        except (OSError, ValueError, TypeError):
            continue
    candidates.sort(key=lambda row: row[:2], reverse=True)
    collision = current_collision(builder, tile)
    if collision is not None and not complete(builder.root, tile, collision):
        collision = None
    if collision is None:
        collision = next((value for _, _, value in candidates if _current_retained_collision(builder, tile, value)), None)
    if collision is None or not complete(builder.root, tile, collision):
        return None
    key_for = ((builder.structural_input_key or builder.input_key)
               if collision.get("pass_kind") == "first" else builder.input_key)
    if key_for(tile) is None:
        return None
    value = candidates[0][2] if candidates else collision
    # A full replacement publishes atomically, so never prefer the retained generation
    # over a complete current manifest. Fresh first passes still keep full visuals.
    fresh = (value is collision and value.get("code") == builder.code())
    result = {**value, "collision": collision["collision"]}
    # Routes track structural support; parked visuals stay with the visual generation.
    if collision.get("roads") is not None:
        result["roads"] = collision["roads"]
    if not fresh:
        result["stale"] = True
        result["collision_source"] = "current"
    return result


def _manifest(builder, tile):
    try:
        return json.loads(builder.manifest(tile).read_text())
    except (OSError, ValueError):
        return None


def _current_retained_collision(builder, tile, value):
    if value.get("collision_code") != builder.collision_code():
        return False
    key = builder.input_key(tile)
    if key is None or value.get("inputs") != key:
        return False
    if "building_keys" in value:
        from turku3d.facade_kit import parameter_hash
        if value.get("facade_params") != parameter_hash(value["building_keys"]):
            return False
    return True
