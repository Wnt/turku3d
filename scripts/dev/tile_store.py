"""Content-addressed tile releases and per-tile entries for the localhost game (tools/dev/localhost/README.md).

Layout:

    $TURKU3D_DATA/tile-store/<key>/            one complete tile dist tree (tileset.json, manifest.json, visual/,
                                               collision/, ground/, roads/, vehicles/, addresses/) and STORE.json;
                                               immutable once renamed into place, APFS clones of the build output
    $TURKU3D_DATA/tile-store/<key>/STORE.json  {"key", "commit", "source", "time", "data_time", "data_parts", "tiles",
                                               "facades_bound"?}
    $TURKU3D_DATA/tile-store/index.jsonl       the same record per entry, in the order entries were added
    $TURKU3D_DATA/tile-store/tiles/<tile>/<key>/   one finished L0 tile from its builder (put_tile): its files at
                                               their dist paths and TILE.json (fragment and provenance)
    $TURKU3D_DATA/tile-store/tiles/index.jsonl the TILE.json records without fragments, in the order they were put

The newest KEEP entries stay, plus the newest older entry for each listed tile no kept entry carries (prune).

The key of a commit hashes what its tile build reads: the git blob ids of the refresh and tile-build inputs
(live_cache.INPUTS) at that commit and the data root's parts (landing_class.data_parts: facade approvals, the
playable tile list, the published layers' JSON manifests). The integrator's tile-build stage puts its output under
the key (source "integrator"). Once the integrator publishes a commit's refresh output, the data root's layers change
with it and the commit's key moves to the live release's, which the service then serves directly.

A per-tile entry is what one builder (`make tile-add`, `make tile-build PUT=1`, the service's quick build) finished
for one L0 tile: its visual tile, its 500 m and 1 km parents and the coarse leaves beside it, its 16 collision tiles
and the ring of collision tiles around them, its ground textures, the parked cars on it, and the road graph and
address pool of the build. Its key hashes the assembler's input keys of those tiles (.assemble-cache.json).
`compose` lays the newest eligible entry of every listed tile over a release (newest entry wins where two share a
parent or a ring cell) and puts the result as a complete entry (source "composed"), so the service serves it like
any release. An entry is left out when the release already has its bytes (absorbed), when the release was seeded
from the data root after the entry was put and carries the tile (superseded), when its tile is not listed in the data
root or its builder's list is not, or when it was built with tile code that never was main's (eligible()).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import landing_class
import live_cache
from landing_class import C

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from turku3d.package.manifest import build_tileset  # noqa: E402  (stdlib-only like config)

KEEP = 30
TILE_KEEP = 3
TILES = "tiles"
PATTERNS = tuple(dict.fromkeys(live_cache.INPUTS["refresh"] + live_cache.INPUTS["tile-build"]))
# Top-level dist directories a per-tile entry carries files in.
DIST_DIRS = ("visual", "collision", "ground", "roads", "addresses", "vehicles")


def root(published):
    return Path(published) / "tile-store"


def tiles_root(published):
    return root(published) / TILES


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _tile_blobs(repo, commit):
    """(path, blob id) of the tile build's inputs at commit, in tree order."""
    for line in git(repo, "ls-tree", "-r", "--full-tree", commit).splitlines():
        meta, name = line.split("\t", 1)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in PATTERNS):
            yield name, meta.split()[2]


def _key(repo, commit, data):
    h = hashlib.sha256(b"turku3d-tile-store v1\0")
    for name, blob in _tile_blobs(repo, commit):
        h.update(f"{name}\0{blob}\n".encode())
    h.update(data)
    return h.hexdigest()


def _data(published):
    return json.dumps(landing_class.data_parts(published), sort_keys=True).encode()


def key(repo, commit, published):
    """Content key of the tile build of commit against the data root published."""
    return _key(repo, commit, _data(published))


def is_ancestor(repo, commit, head):
    return (
        subprocess.call(
            ["git", "merge-base", "--is-ancestor", commit, head],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        == 0
    )


def code_key(repo, commit=None):
    """Hash of the tile code (the blob ids key() reads) at commit; commit None: the working tree with its edits."""
    blobs = dict(_tile_blobs(repo, commit or "HEAD"))
    if commit is None:
        edited = git(repo, "diff", "--name-only", "HEAD").splitlines()
        edited += git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
        for name in edited:
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in PATTERNS):
                continue
            path = Path(repo) / name
            if path.is_file():
                blobs[name] = git(repo, "hash-object", str(path)).strip()
            else:
                blobs.pop(name, None)
    h = hashlib.sha256(b"turku3d-tile-code v1\0")
    for name in sorted(blobs):
        h.update(f"{name}\0{blobs[name]}\n".encode())
    return h.hexdigest()


def provenance(repo, published):
    """{commit, code, mainline} of a builder checkout: mainline is its merge-base with main when the checkout's
    tile code is exactly that commit's (no unlanded tile code edits), else None."""
    commit = git(repo, "rev-parse", "HEAD").strip()
    code = code_key(repo)
    try:
        base = git(repo, "merge-base", "HEAD", "main").strip()
    except subprocess.CalledProcessError:
        base = None
    return {
        "commit": commit,
        "code": code,
        "mainline": base if base and code_key(repo, base) == code else None,
    }


def data_key(published):
    return hashlib.sha256(
        json.dumps(landing_class.data_parts(published), sort_keys=True).encode()
    ).hexdigest()


def entry(published, store_key):
    """The STORE.json record of a complete entry, or None."""
    record = root(published) / store_key / "STORE.json"
    try:
        return json.loads(record.read_text())
    except (OSError, ValueError):
        return None


def _index(directory, record):
    with (directory / "index.jsonl").open("a") as index:
        index.write(json.dumps(record) + "\n")


def put(published, tiles, store_key, commit, source, built=None, seeded=None, extra=None):
    """Clone the tile dist `tiles` into the store under store_key; an existing entry is kept.

    seeded: {"time", "parts"}, when the build read the data root and its data parts then; compose() orders releases
    and per-tile entries by the time, served() finds what changed in the data root since by the parts.
    extra: further STORE.json fields (the integrator records `facades_bound`, which served.py ships)."""
    store = root(published)
    target = store / store_key
    if target.exists() or not (Path(tiles) / "manifest.json").is_file():
        return target if target.exists() else None
    store.mkdir(parents=True, exist_ok=True)
    temp = store / f".{store_key}.{os.getpid()}.tmp"
    shutil.rmtree(temp, ignore_errors=True)
    # APFS clones share the bytes with the build output; copytree is the portable fallback.
    if subprocess.call(["cp", "-cR", str(tiles), str(temp)], stderr=subprocess.DEVNULL):
        shutil.rmtree(temp, ignore_errors=True)
        shutil.copytree(tiles, temp, symlinks=False)
    record = {
        "key": store_key,
        "commit": commit,
        "source": source,
        "time": time.time(),
        "data_time": (seeded or {}).get("time"),
        "data_parts": (seeded or {}).get("parts"),
        "tiles": list(built) if built else None,
        **(extra or {}),
    }
    (temp / "STORE.json").write_text(json.dumps(record) + "\n")
    try:
        temp.rename(target)
    except OSError:
        shutil.rmtree(temp, ignore_errors=True)
        return target if target.exists() else None
    _index(store, record)
    prune(published)
    return target


def base_parts(base_dir):
    """The data parts the release in base_dir was built from: STORE.json's (recorded at its seed) or, for a
    published release, its own (landing_class.release_parts); None when unknown."""
    base_dir = Path(base_dir)
    try:
        parts = json.loads((base_dir / "STORE.json").read_text()).get("data_parts")
    except (OSError, ValueError):
        parts = None
    if parts is None and (base_dir.parent / "live-build.json").is_file():
        parts = landing_class.release_parts(base_dir.parent)
    return parts


def entry_tiles(path):
    """The L0 tiles an entry's manifest carries (playable and coarse leaves)."""
    try:
        visual = json.loads((path / "manifest.json").read_text())["visual"]
    except (OSError, ValueError, KeyError):
        return set()
    return {tid for tid, e in visual.items() if e.get("level") == 0}


def prune(published, keep=KEEP):
    """Remove all but the newest `keep` entries; the newest entry carrying a listed tile always stays.

    The guarantee: every tile in playable-tiles.json keeps at least one store entry, even when the
    newest `keep` builds (a shrunk list, a failed tile) lack it. The per-tile entries under tiles/ are pruned by
    prune_tiles."""
    store = root(published)
    entries = sorted(
        (
            path
            for path in store.iterdir()
            if path.is_dir() and not path.name.startswith(".") and path.name != TILES
        ),
        key=lambda path: path.stat().st_mtime,
    )
    kept = entries[-keep:]
    missing = set(landing_class.listed_tiles(published))
    for path in kept:
        missing -= entry_tiles(path)
    for path in reversed(entries[:-keep]):
        carried = missing & entry_tiles(path)
        if carried:
            kept.append(path)
            missing -= carried
    for path in entries:
        if path not in kept:
            shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------
# Per-tile entries
# ---------------------------------------------------------------------------------------------------------------


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


def fragment(tiles_dir, tile_id):
    """(fragment, files) of L0 tile tile_id in the dist tiles_dir: the manifest entries the tile owns and the
    dist-relative files they need."""
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
        if manifest.get(name):
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
        "roads": manifest.get("roads"),
        "addresses": manifest.get("addresses"),
        "keys": keys,
    }, sorted(files)


def tile_key(frag):
    """A per-tile entry's key: the assembler's input keys of everything it carries."""
    return hashlib.sha256(
        json.dumps(["turku3d-tile v1", frag["tile"], frag["keys"]], sort_keys=True).encode()
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
    """True when the release manifest has every visual and collision tile of the fragment with the same bytes."""
    return all(
        manifest[kind].get(tid, {}).get("sha256") == e["sha256"]
        for kind in ("visual", "collision")
        for tid, e in frag[kind].items()
    )


def put_tile(published, tiles_dir, tile_id, *, source, task, listed, origin):
    """Put the finished L0 tile tile_id of the dist tiles_dir as a per-tile entry; an existing entry is kept.

    origin is provenance(): the builder's commit, tile code and mainline commit."""
    tiles_dir = Path(tiles_dir)
    frag, files = fragment(tiles_dir, tile_id)
    tile_key_ = tile_key(frag)
    directory = tiles_root(published) / frag["tile"]
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
    _index(tiles_root(published), record)
    prune_tiles(published, frag["tile"])
    return target


def own_bytes_differ(frag, manifest):
    """True when the release manifest lacks the fragment's own visual tile or one of its own 16 collision tiles, or
    has other bytes for it (parents and ring cells it shares with neighbours do not count)."""
    tile = C.VisualTile.parse(frag["tile"])
    own = {c.id for c in tile.collision_tiles()}
    return manifest["visual"].get(tile.id, {}).get("sha256") != frag["visual"][tile.id][
        "sha256"
    ] or any(
        manifest["collision"].get(cid, {}).get("sha256") != e["sha256"]
        for cid, e in frag["collision"].items()
        if cid in own
    )


def put_tiles(published, tiles_dir, tiles, *, source, task, listed, repo, against=None):
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
        frag, _ = fragment(tiles_dir, tile)
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
            )
        )
    return put


def tile_entries(published, tile):
    """The TILE.json records of a tile's complete entries, newest first."""
    directory = tiles_root(published) / tile
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


# ---------------------------------------------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------------------------------------------


def base_time(base_dir):
    """When the release in base_dir read the data root: STORE.json's or live-build.json's data_time, else None."""
    base_dir = Path(base_dir)
    for record in (base_dir / "STORE.json", base_dir.parent / "live-build.json"):
        try:
            value = json.loads(record.read_text()).get("data_time")
        except (OSError, ValueError):
            continue
        if value is not None:
            return value
    return None


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
    root_ = tiles_root(published)
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
        base_time(base_dir),
        code_key(repo, head),
        lambda c: is_ancestor(repo, c, head),
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
        "dir": str(root(published) / composed_key),
        "key": composed_key,
        "source": "composed",
        "tiles": [r["tile"] for r in applied],
        "skipped": skipped,
    }
    if entry(published, composed_key):
        return result
    manifest, tileset = compose_manifest(base, applied)
    store = root(published)
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
        "data_time": base_time(base_dir),
        "data_parts": base_parts(base_dir),
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
    _index(store, record)
    prune(published)
    return result


def base_release(repo, head, published, data):
    """{dir, key, source, commit} of the release the per-tile entries are composed over for main's commit head:
    the integrator's entry for head's key; the live release when its commit has that key (a code-only landing);
    else the newest by data_time of the live release and the integrator's entries of head's ancestors (a landing
    whose build is not in yet keeps the newest build of its history, never an older one)."""
    full = _key(repo, head, data)
    if entry(published, full):
        return {
            "dir": str(root(published) / full),
            "key": full,
            "source": "integrator",
            "commit": head,
        }
    live = Path(published) / "live"
    live_dir = (live / "tiles").resolve()
    try:
        live_commit = json.loads((live / "live-build.json").read_text())["commit"]
        live_key = _key(repo, live_commit, data)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        live_commit, live_key = None, None
    best = {
        "dir": str(live_dir),
        "key": live_key or str(live_dir),
        "source": "live",
        "commit": live_commit,
    }
    if live_key == full:
        return best
    newest = base_time(live_dir) or 0
    try:
        rows = [
            json.loads(line) for line in (root(published) / "index.jsonl").read_text().splitlines()
        ]
    except (OSError, ValueError):
        rows = []
    rows = [
        r for r in rows if r.get("source") == "integrator" and (r.get("data_time") or 0) > newest
    ]
    for record in sorted(rows, key=lambda r: r["data_time"], reverse=True):
        if entry(published, record["key"]) and is_ancestor(repo, record["commit"], head):
            return {
                "dir": str(root(published) / record["key"]),
                "key": record["key"],
                "source": "integrator",
                "commit": record["commit"],
            }
    return best


def served(repo, head, published):
    """What the localhost game streams for main's commit head (and what live and Pages publish with it):
    {key, delta, base, served}. key is head's tile store key, base the release (base_release), delta the data parts
    that changed since base was built (a quick build can improve on it), served compose()'s result over base."""
    parts = landing_class.data_parts(published)
    data = json.dumps(parts, sort_keys=True).encode()
    base = base_release(repo, head, published, data)
    return {
        "key": _key(repo, head, data),
        "delta": landing_class.data_delta(parts, base_parts(base["dir"])),
        "base": base,
        "served": compose(repo, head, published, base["dir"], base["key"]),
    }


def quick_build(repo, commit, published, work, tiles, base):
    """Tile-build `tiles` of the checkout repo on the published layers into work, starting from the release base;
    put every tile whose bytes changed as a per-tile entry (source "localhost"). The entry paths."""
    import live_build

    work = Path(work)
    shutil.rmtree(work, ignore_errors=True)
    live_build.seed(work, Path(published))
    # Start from the release the service streams, so unaffected tiles keep their bytes.
    shutil.rmtree(work / "dist/tiles", ignore_errors=True)
    live_build._clone(Path(base), work / "dist/tiles")
    env = {
        **os.environ,
        "TURKU3D_WORK": str(work),
        "TURKU3D_DATA": str(published),
        "TILE_DIST": str(work / "dist/tiles"),
    }
    command = ["make", "tile-build", "AREA=playable-tiles", "T=" + " ".join(tiles)]
    result = subprocess.run(
        command, cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    (work / "tile-build.log").write_text(result.stdout)
    if result.returncode:
        raise SystemExit(
            f"{' '.join(command)} failed ({result.returncode}): {work / 'tile-build.log'}"
        )
    return put_tiles(
        published,
        work / "dist/tiles",
        tiles,
        source="localhost",
        task="localhost-game",
        listed=landing_class.listed_tiles(published),
        repo=repo,
        against=base,
    )


def builder_listed(work, published):
    """The list a builder followed: its sandbox list when it has one, else the data root's."""
    own = Path(work) / C.PLAYABLE_TILES_FILE
    return C.read_playable_tiles(own) if own.is_file() else landing_class.listed_tiles(published)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("key", "served", "quick-build", "put", "put-tiles", "compose"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, default=Path.cwd())
        p.add_argument("--commit", default="HEAD")
        p.add_argument("--published", type=Path, default=os.environ.get("TURKU3D_DATA"))
        if name == "quick-build":
            p.add_argument("--work", type=Path, required=True)
            p.add_argument(
                "--base", type=Path, required=True, help="tile dist the build starts from"
            )
            p.add_argument("--tiles", nargs="+", required=True)
        if name == "put":
            p.add_argument("--tiles-dir", type=Path, required=True)
            p.add_argument("--source", default="integrator")
        if name == "put-tiles":
            p.add_argument("--tiles-dir", type=Path, required=True)
            p.add_argument(
                "--tiles", nargs="*", help="L0 tiles to offer (default: the builder's list)"
            )
            p.add_argument("--source", default="tile-build")
            p.add_argument("--work", type=Path, default=os.environ.get("TURKU3D_WORK"))
        if name == "compose":
            p.add_argument("--base-dir", type=Path, required=True)
            p.add_argument("--base-key", required=True)
    args = parser.parse_args(argv)
    commit = git(args.repo, "rev-parse", args.commit).strip()
    if args.command == "key":
        print(key(args.repo, commit, args.published))
    elif args.command == "served":
        print(json.dumps(served(args.repo, commit, args.published)))
    elif args.command == "put":
        print(
            put(
                args.published,
                args.tiles_dir,
                key(args.repo, commit, args.published),
                commit,
                args.source,
            )
        )
    elif args.command == "put-tiles":
        listed = builder_listed(args.work, args.published)
        started = time.time()
        entries = put_tiles(
            args.published,
            args.tiles_dir,
            args.tiles or listed,
            source=args.source,
            task=os.environ.get("TURKU3D_SANDBOX"),
            listed=listed,
            repo=args.repo,
        )
        for path in entries:
            print(
                f"put-tile {Path(path).parent.name} {Path(path).name} ({time.time() - started:.2f}s)"
            )
        if not entries:
            print("put-tile: every tile already has the live release's bytes")
    elif args.command == "compose":
        print(json.dumps(compose(args.repo, commit, args.published, args.base_dir, args.base_key)))
    else:
        print(
            json.dumps(
                [
                    str(p)
                    for p in quick_build(
                        args.repo, commit, args.published, args.work, args.tiles, args.base
                    )
                ]
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
