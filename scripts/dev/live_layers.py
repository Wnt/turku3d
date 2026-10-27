"""Select changed layer tiles and shipped collision files for data builds."""

import filecmp

from landing_class import C, released_tiles

# Layer-wide files that turku3d.tile_layer producers combine from their per-tile records.
PER_TILE_SUMMARIES = {
    "props/instances.json",
    "props/metrics.json",
    "roads/roadgraph.json",
    "vegetation/trees.json",
    "traffic_static/placements.json",
    "structures/metrics.json",
    "structures/report.md",
    "buildings_outer/metrics.json",
}


def affected_tiles(previous, work, listed):
    """Map changed layer meshes to listed visual tiles; ambiguous changes rebuild all of them.

    A tile the previous release did not list is always affected, and so are its listed edge
    neighbours: their seam rows and border collision tiles change with it. The list record a
    layer keeps (PLAYABLE_TILES.json) is explained by the list itself. A per-tile layer's
    `<tile>.records.json` maps to its tile, and the layer-wide summaries it combines from those
    records (PER_TILE_SUMMARIES) are explained by them."""
    old = previous / "layers"
    new = work / "build/layers"
    if not old.is_dir() or not new.is_dir():
        return tuple(listed)
    if any(path.is_symlink() for root in (old, new) for path in root.rglob("*")):
        return tuple(listed)
    added = set(listed) - set(released_tiles(previous))
    changed = set(added)
    for tile in map(C.VisualTile.parse, added):
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            changed.add(C.VisualTile(0, tile.i + di, tile.j + dj).id)
    names = {p.relative_to(old) for p in old.rglob("*") if p.is_file()}
    names |= {p.relative_to(new) for p in new.rglob("*") if p.is_file()}
    for name in names:
        before, after = old / name, new / name
        if before.is_file() and after.is_file() and filecmp.cmp(before, after, shallow=False):
            continue
        stem = name.name
        if stem.endswith(".vis.glb") and stem.split(".vis.glb")[0] in listed:
            changed.add(stem.split(".vis.glb")[0])
        elif stem.endswith(".col.npz") and stem.startswith("c_"):
            changed.add(C.CollisionTile.parse(stem.split(".")[0]).visual_parent().id)
        elif stem.endswith(".records.json"):
            changed.add(stem.removesuffix(".records.json"))
        elif stem in ("MANIFEST.json", "PLAYABLE_TILES.json") or str(name) in PER_TILE_SUMMARIES:
            continue
        else:
            return tuple(listed)
    return tuple(tile for tile in listed if tile in changed)


def affected_collision_files(previous, work, selected):
    """Validate changed shipped colliders; a validator-only change checks the whole selection."""
    current = work / "dist/tiles/collision"
    old = previous / "tiles/collision" if previous else None
    files = tuple(sorted(path for tile in selected for path in (current / tile).glob("*.col")))
    if not old or not old.is_dir():
        return files
    changed = tuple(
        path
        for path in files
        if not (old / path.parent.name / path.name).is_file()
        or not filecmp.cmp(old / path.parent.name / path.name, path, shallow=False)
    )
    return changed or files
