"""Per-tile tile store entries and their composition over a release (tools/dev/localhost/README.md).

A per-tile entry, $TURKU3D_DATA/tile-store/tiles/<tile>/<key>/, is what one builder (`make tile-add`,
`make tile-build PUT=1`, the localhost service's quick build) finished for one L0 tile: its visual tile, its 500 m and
1 km parents and the coarse leaves beside it, its 16 collision tiles and the ring of collision tiles around them, its
ground textures, the parked cars on it, and the road graph and address pool of the build, at their dist paths, with
TILE.json (the manifest fragment and provenance). Its key hashes the assembler's input keys of those tiles
(.assemble-cache.json) and the shared road and address assets; tiles/index.jsonl lists the records in the order they were put.

`compose` lays the newest eligible entry of every listed tile over a release (a newer entry wins where two share a
parent or a ring cell) and puts the result as a complete release entry (source "composed"), so the localhost game
serves it like any release. An entry is left out when the release already has its bytes (absorbed), when the release
was seeded from the data root after the entry was put and carries the tile (superseded), when its tile or its builder's
list is not listed in the data root, or when it was built with tile code that never was main's (eligible()).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import landing_class
import tile_store
from landing_class import C

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from turku3d.package.manifest import build_tileset  # noqa: E402  (stdlib-only like config)

TILE_KEEP = 3
# Top-level dist directories a per-tile entry carries files in.
DIST_DIRS = ("visual", "collision", "ground", "roads", "addresses", "vehicles")


def provenance(repo, published):
    """{commit, code, mainline} of a builder checkout: mainline is its merge-base with main when the checkout's
    tile code is exactly that commit's (no unlanded tile code edits), else None."""
    commit = tile_store.git(repo, "rev-parse", "HEAD").strip()
    code = tile_store.code_key(repo)
    try:
        base = tile_store.git(repo, "merge-base", "HEAD", "main").strip()
    except subprocess.CalledProcessError:
        base = None
    return {
        "commit": commit,
        "code": code,
        "mainline": base if base and tile_store.code_key(repo, base) == code else None,
    }


def data_key(published):
    return hashlib.sha256(
        json.dumps(landing_class.data_parts(published), sort_keys=True).encode()
    ).hexdigest()


def _ring(tile):
    """The tile's 16 collision tiles and the ring of collision tiles around them."""
    n = round(C.VISUAL_TILE_SIZE / C.COLLISION_TILE_SIZE)
    return {
        f"c_{i}_{j}"
        for i in range(tile.i * n - 1, tile.i * n + n + 1)
        for j in range(tile.j * n - 1, tile.j * n + n + 1)
    }


def _in(tile, car):
    return C.VisualTile.at(car["x"], car["z"]).id == tile.id


def fragment(tiles_dir, tile_id, *, tile_only=False):
    """(fragment, files) of L0 tile tile_id in the dist tiles_dir: the manifest entries the tile owns and the
    dist-relative files they need. tile_only leaves global roads and addresses to the base release."""
    tiles_dir = Path(tiles_dir)
    manifest = json.loads((tiles_dir / "manifest.json").read_text())
    try:
        cache = json.loads((tiles_dir / ".assemble-cache.json").read_text())
    except (OSError, ValueError):
        cache = {}
    tile = C.VisualTile.parse(tile_id)
    own = manifest["visual"].get(tile.id)
    if tile.level != 0 or own is None or "coarse_dtm" in own:
        raise ValueError(f"{tile_id}: not a playable L0 tile of {tiles_dir}")
    parents = [tile.parent()]
    parents.append(parents[0].parent())
    ids = [tile.id] + [p.id for p in parents if p]
    # REPLACE refinement drops the whole 500 m parent: its quadrants without a playable tile are coarse leaves.
    ids += [
        child.id
        for child in parents[0].children()
        if child != tile and "coarse_dtm" in manifest["visual"].get(child.id, {})
    ]
    visual = {tid: manifest["visual"][tid] for tid in ids if tid in manifest["visual"]}
    cells = sorted(_ring(tile) & manifest["collision"].keys())
    collision = {cid: manifest["collision"][cid] for cid in cells}
    vehicles = [car for car in manifest.get("vehicles", []) if _in(tile, car)]
    models = {
        name: e
        for name, e in manifest.get("vehicle_models", {}).items()
        if any(f"{car['model']}/{car['color']}" == name for car in vehicles)
    }
    files = {e["url"] for e in [*visual.values(), *collision.values(), *models.values()]}
    files |= {image["url"] for e in visual.values() for image in e.get("shared_images", [])}
    for name in ("roads", "addresses"):
        if not tile_only and manifest.get(name):
            files.add(manifest[name]["url"])
    for directory in (tiles_dir / "ground" / tile.id, tiles_dir / "ground/library"):
        if directory.is_dir():
            files |= {str(p.relative_to(tiles_dir)) for p in directory.rglob("*") if p.is_file()}
    keys = {tid: cache.get(tid, e["sha256"]) for tid, e in visual.items()}
    keys |= {
        f"collision:{cid}": cache.get(f"collision:{cid}", e["sha256"])
        for cid, e in collision.items()
    }
    return {
        "tile": tile.id,
        "visual": visual,
        "collision": collision,
        "vehicles": vehicles,
        "vehicle_models": models,
        "roads": None if tile_only else manifest.get("roads"),
        "addresses": None if tile_only else manifest.get("addresses"),
        "keys": keys,
    }, sorted(files)


def tile_key(frag):
    """A per-tile entry's key: the assembler's input keys of everything it carries."""
    return hashlib.sha256(
        json.dumps(
            [
                "turku3d-tile v2",
                frag["tile"],
                frag["keys"],
                {name: (frag.get(name) or {}).get("sha256") for name in ("roads", "addresses")},
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _clone_files(source, target, files):
    """Clone dist-relative files from source to target, one `cp -c` per directory (copy as the fallback)."""
    groups = {}
    for name in files:
        groups.setdefault(str(Path(name).parent), []).append(name)
    for directory, names in groups.items():
        (target / directory).mkdir(parents=True, exist_ok=True)
        sources = [str(source / name) for name in names]
        if subprocess.call(
            ["cp", "-c", *sources, str(target / directory) + "/"], stderr=subprocess.DEVNULL
        ):
            for name in names:
                shutil.copyfile(source / name, target / name)


def same_bytes(frag, manifest):
    """True when the release carries the fragment's tile and shared assets with the same bytes."""
    return all(
        manifest[kind].get(tid, {}).get("sha256") == e["sha256"]
        for kind in ("visual", "collision")
        for tid, e in frag[kind].items()
    ) and all(
        not frag.get(name) or (manifest.get(name) or {}).get("sha256") == frag[name]["sha256"]
        for name in ("roads", "addresses")
    )


def put_tile(published, tiles_dir, tile_id, *, source, task, listed, origin, tile_only=False):
    """Put the finished L0 tile tile_id of the dist tiles_dir as a per-tile entry; an existing entry is kept.

    origin is provenance(): the builder's commit, tile code and mainline commit."""
    tiles_dir = Path(tiles_dir)
    frag, files = fragment(tiles_dir, tile_id, tile_only=tile_only)
    tile_key_ = tile_key(frag)
    directory = tile_store.tiles_root(published) / frag["tile"]
    target = directory / tile_key_
    if target.exists():
        return target
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f".{tile_key_}.{os.getpid()}.tmp"
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir()
    _clone_files(tiles_dir, temp, files)
    record = {
        "key": tile_key_,
        "tile": frag["tile"],
        "time": time.time(),
        "source": source,
        "task": task,
        **origin,
        "data_key": data_key(published),
        "listed": sorted(listed),
        "files": len(files),
    }
    (temp / "TILE.json").write_text(json.dumps({**record, "fragment": frag}) + "\n")
    try:
        temp.rename(target)
    except OSError:
        shutil.rmtree(temp, ignore_errors=True)
        return target if target.exists() else None
    tile_store._index(tile_store.tiles_root(published), record)
    prune_tiles(published, frag["tile"])
    return target


def own_bytes_differ(frag, manifest):
    """True when the release manifest lacks the fragment's own visual tile or one of its own 16 collision tiles, or
    has other bytes for it (parents and ring cells it shares with neighbours do not count)."""
    tile = C.VisualTile.parse(frag["tile"])
    own = {c.id for c in tile.collision_tiles()}
    return (
        any(
            frag.get(name) and (manifest.get(name) or {}).get("sha256") != frag[name]["sha256"]
            for name in ("roads", "addresses")
        )
        or manifest["visual"].get(tile.id, {}).get("sha256") != frag["visual"][tile.id]["sha256"]
        or any(
            manifest["collision"].get(cid, {}).get("sha256") != e["sha256"]
            for cid, e in frag["collision"].items()
            if cid in own
        )
    )


def put_tiles(
    published, tiles_dir, tiles, *, source, task, listed, repo, against=None, tile_only=False
):
    """put_tile every tile of `tiles` whose own bytes differ from the release `against` (default: live); the
    entry paths."""
    against = Path(against) if against else Path(published) / "live/tiles"
    try:
        release = json.loads((against / "manifest.json").read_text())
    except (OSError, ValueError):
        release = {"visual": {}, "collision": {}}
    origin = provenance(repo, published)
    built = json.loads((Path(tiles_dir) / "manifest.json").read_text())["visual"]
    put = []
    for tile in tiles:
        if tile not in built or "coarse_dtm" in built[tile]:
            continue
        frag, _ = fragment(tiles_dir, tile, tile_only=tile_only)
        if not own_bytes_differ(frag, release):
            continue
        put.append(
            put_tile(
                published,
                tiles_dir,
                tile,
                source=source,
                task=task,
                listed=listed,
                origin=origin,
                tile_only=tile_only,
            )
        )
    return put


def tile_entries(published, tile):
    """The TILE.json records of a tile's complete entries, newest first."""
    directory = tile_store.tiles_root(published) / tile
    records = []
    if directory.is_dir():
        for path in directory.iterdir():
            try:
                record = json.loads((path / "TILE.json").read_text())
            except (OSError, ValueError):
                continue
            records.append({**record, "dir": str(path)})
    return sorted(records, key=lambda r: r["time"], reverse=True)


def prune_tiles(published, tile, keep=TILE_KEEP):
    for record in tile_entries(published, tile)[keep:]:
        shutil.rmtree(record["dir"], ignore_errors=True)


def _playable(manifest, tile):
    entry = manifest["visual"].get(tile)
    return entry is not None and "coarse_dtm" not in entry


def eligible(record, head_code, is_ancestor):
    """Built with tile code that is main's: a mainline commit of main's history, or main's current code."""
    return bool(record.get("mainline") and is_ancestor(record["mainline"])) or (
        record.get("code") == head_code
    )


def applicable(published, base, base_data_time, head_code, is_ancestor):
    """(applied, skipped): the newest eligible entry per listed tile that is neither absorbed by the release base
    (a manifest) nor superseded by it; skipped maps tile -> reason."""
    listed = set(landing_class.listed_tiles(published))
    applied, skipped = [], {}
    root_ = tile_store.tiles_root(published)
    for tile in sorted(p.name for p in root_.iterdir() if p.is_dir()) if root_.is_dir() else []:
        if tile not in listed:
            skipped[tile] = "not listed"
            continue
        records = [r for r in tile_entries(published, tile) if eligible(r, head_code, is_ancestor)]
        if not records:
            skipped[tile] = "no entry built with main's tile code"
            continue
        record = records[0]
        if not set(record.get("listed", [])) <= listed:
            skipped[tile] = "its builder's list is not listed yet"
            continue
        frag = json.loads((Path(record["dir"]) / "TILE.json").read_text())["fragment"]
        if same_bytes(frag, base):
            skipped[tile] = "absorbed"
        elif (
            base_data_time is not None and base_data_time > record["time"] and _playable(base, tile)
        ):
            skipped[tile] = "superseded"
        else:
            applied.append({**record, "fragment": frag})
    return sorted(applied, key=lambda r: r["time"]), skipped


def compose_manifest(base, applied):
    """The base manifest with the fragments laid over it, oldest first; plus its tileset (dicts)."""
    manifest = json.loads(json.dumps(base))
    for record in applied:
        frag = record["fragment"]
        tile = C.VisualTile.parse(frag["tile"])
        for tid, e in frag["visual"].items():
            # A coarse leaf never replaces a playable tile another builder finished.
            if "coarse_dtm" in e and _playable(manifest, tid):
                continue
            manifest["visual"][tid] = e
        manifest["collision"].update(frag["collision"])
        manifest["vehicles"] = [
            car for car in manifest.get("vehicles", []) if not _in(tile, car)
        ] + frag["vehicles"]
        manifest["vehicle_models"] = {
            **manifest.get("vehicle_models", {}),
            **frag["vehicle_models"],
        }
        for name in ("roads", "addresses"):
            if frag.get(name):
                manifest[name] = frag[name]
    manifest["visual"] = {k: manifest["visual"][k] for k in sorted(manifest["visual"])}
    manifest["collision"] = {k: manifest["collision"][k] for k in sorted(manifest["collision"])}
    # The same tileset the assembler writes for these visual entries (DistTree.commit), so a release that converged
    # on the same bytes has the same tileset bytes too.
    tileset = build_tileset(manifest["visual"], f"turku3d {manifest.get('code', 'nogit')}")
    return manifest, tileset


def _dumps(value):
    return (json.dumps(value, indent=1) + "\n").encode()


def compose(repo, head, published, base_dir, base_key):
    """{dir, key, source, tiles, skipped}: base_dir itself when no per-tile entry applies, else the composed entry."""
    base_dir = Path(base_dir)
    base = json.loads((base_dir / "manifest.json").read_text())
    applied, skipped = applicable(
        published,
        base,
        tile_store.base_time(base_dir),
        tile_store.code_key(repo, head),
        lambda c: tile_store.is_ancestor(repo, c, head),
    )
    if not applied:
        return {
            "dir": str(base_dir),
            "key": base_key,
            "source": None,
            "tiles": [],
            "skipped": skipped,
        }
    composed_key = hashlib.sha256(
        json.dumps(["turku3d-composed v1", base_key, [r["key"] for r in applied]]).encode()
    ).hexdigest()
    result = {
        "dir": str(tile_store.root(published) / composed_key),
        "key": composed_key,
        "source": "composed",
        "tiles": [r["tile"] for r in applied],
        "skipped": skipped,
    }
    if tile_store.entry(published, composed_key):
        return result
    manifest, tileset = compose_manifest(base, applied)
    store = tile_store.root(published)
    temp = store / f".{composed_key}.{os.getpid()}.tmp"
    shutil.rmtree(temp, ignore_errors=True)
    if subprocess.call(["cp", "-cR", str(base_dir), str(temp)], stderr=subprocess.DEVNULL):
        shutil.rmtree(temp, ignore_errors=True)
        shutil.copytree(base_dir, temp, symlinks=False)
    for record in applied:
        source = Path(record["dir"])
        files = [
            str(p.relative_to(source))
            for top in DIST_DIRS
            if (source / top).is_dir()
            for p in (source / top).rglob("*")
            if p.is_file()
        ]
        _clone_files(source, temp, files)
    tileset_bytes = _dumps(tileset)
    manifest["tileset"] = {
        "url": "tileset.json",
        "sha256": hashlib.sha256(tileset_bytes).hexdigest(),
        "bytes": len(tileset_bytes),
    }
    try:
        cache = json.loads((base_dir / ".assemble-cache.json").read_text())
    except (OSError, ValueError):
        cache = {}
    for record in applied:
        cache.update(record["fragment"]["keys"])
    files = {
        "tileset.json": tileset_bytes,
        "manifest.json": _dumps(manifest),
        ".assemble-cache.json": (json.dumps(cache, sort_keys=True) + "\n").encode(),
    }
    for name, data in files.items():
        (temp / name).unlink(missing_ok=True)
        (temp / name).write_bytes(data)
    record = {
        "key": composed_key,
        "commit": head,
        "source": "composed",
        "time": time.time(),
        "data_time": tile_store.base_time(base_dir),
        "data_parts": tile_store.base_parts(base_dir),
        "tiles": result["tiles"],
        "base": base_key,
        "entries": [{"tile": r["tile"], "key": r["key"], "task": r.get("task")} for r in applied],
    }
    (temp / "STORE.json").unlink(missing_ok=True)
    (temp / "STORE.json").write_text(json.dumps(record) + "\n")
    try:
        temp.rename(store / composed_key)
    except OSError:
        shutil.rmtree(temp, ignore_errors=True)
        return result
    tile_store._index(store, record)
    tile_store.prune(published)
    return result


def builder_listed(work, published):
    """The list a builder followed: its sandbox list when it has one, else the data root's."""
    own = Path(work) / C.PLAYABLE_TILES_FILE
    return C.read_playable_tiles(own) if own.is_file() else landing_class.listed_tiles(published)
