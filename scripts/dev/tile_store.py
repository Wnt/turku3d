"""Content-addressed tile releases and per-tile entries for the localhost game (tools/dev/localhost/README.md).

Layout:

    $TURKU3D_DATA/tile-store/<key>/            one complete tile dist tree (tileset.json, manifest.json, visual/,
                                               collision/, ground/, roads/, vehicles/, addresses/) and STORE.json;
                                               immutable once renamed into place, APFS clones of the build output
    $TURKU3D_DATA/tile-store/<key>/STORE.json  {"key", "commit", "source", "time", "data_time", "data_parts", "tiles",
                                               "facades_bound"?}
    $TURKU3D_DATA/tile-store/index.jsonl       the same record per entry, in the order entries were added
    $TURKU3D_DATA/tile-store/tiles/            per-tile entries (tile_compose.py)

The newest KEEP entries stay, plus the newest older entry for each listed tile no kept entry carries (prune).

The key of a commit hashes what its tile build reads: the git blob ids of the refresh and tile-build inputs
(live_cache.INPUTS) at that commit and the data root's parts (landing_class.data_parts: facade approvals, the
playable tile list, the published layers' JSON manifests). The integrator's tile-build stage puts its output under
the key (source "integrator"). Once the integrator publishes a commit's refresh output, the data root's layers change
with it and the commit's key moves to the live release's, which the service then serves directly.

Per-tile entries (tile-store/tiles/, tile_compose.py) are laid over the release served() picks for main's commit.
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

KEEP = 30
TILES = "tiles"
PATTERNS = tuple(dict.fromkeys(live_cache.INPUTS["refresh"] + live_cache.INPUTS["tile-build"]))


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


def key(repo, commit, published, release=None):
    """Content key using the release's recorded inputs, or the current data root."""
    data = (
        json.dumps(landing_class.release_parts(release), sort_keys=True).encode()
        if release
        else _data(published)
    )
    return _key(repo, commit, data)


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


def put(published, tiles, store_key, commit, source, built=None, extra=None, seeded=None):
    """Clone the tile dist `tiles` into the store under store_key; an existing entry is kept.

    extra: further STORE.json fields (the integrator records `facades_bound`, which served.py ships).
    seeded: {"time", "parts"}, when the build read the data root and its data parts then; compose() orders releases
    and per-tile entries by the time, served() finds what changed in the data root since by the parts."""
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
    tile_compose.prune_tiles."""
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
        live_key = key(repo, live_commit, published, release=live)
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
    import tile_compose

    parts = landing_class.data_parts(published)
    data = json.dumps(parts, sort_keys=True).encode()
    base = base_release(repo, head, published, data)
    return {
        "key": _key(repo, head, data),
        "delta": landing_class.data_delta(parts, base_parts(base["dir"])),
        "base": base,
        "served": tile_compose.compose(repo, head, published, base["dir"], base["key"]),
    }


def quick_build(repo, commit, published, work, tiles, base):
    """Tile-build `tiles` of the checkout repo on the published layers into work, starting from the release base;
    put every tile whose bytes changed as a per-tile entry (source "localhost"). The entry paths."""
    import live_build
    import tile_compose

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
    return tile_compose.put_tiles(
        published,
        work / "dist/tiles",
        tiles,
        source="localhost",
        task="localhost-game",
        listed=landing_class.listed_tiles(published),
        repo=repo,
        against=base,
    )


def main(argv=None):
    import tile_compose

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("key", "served", "quick-build", "put", "put-tiles", "compose"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, default=Path.cwd())
        p.add_argument("--commit", default="HEAD")
        p.add_argument("--published", type=Path, default=os.environ.get("TURKU3D_DATA"))
        if name == "key":
            p.add_argument("--release", type=Path, help="key of what this release was built from")
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
            p.add_argument(
                "--tile-only",
                action="store_true",
                help="preserve served global roads and addresses",
            )
            p.add_argument("--work", type=Path, default=os.environ.get("TURKU3D_WORK"))
        if name == "compose":
            p.add_argument("--base-dir", type=Path, required=True)
            p.add_argument("--base-key", required=True)
    args = parser.parse_args(argv)
    commit = git(args.repo, "rev-parse", args.commit).strip()
    if args.command == "key":
        print(key(args.repo, commit, args.published, args.release))
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
        listed = tile_compose.builder_listed(args.work, args.published)
        started = time.time()
        entries = tile_compose.put_tiles(
            args.published,
            args.tiles_dir,
            args.tiles or listed,
            source=args.source,
            task=os.environ.get("TURKU3D_SANDBOX"),
            listed=listed,
            repo=args.repo,
            tile_only=args.tile_only,
        )
        for path in entries:
            print(
                f"put-tile {Path(path).parent.name} {Path(path).name} ({time.time() - started:.2f}s)"
            )
        if not entries:
            print("put-tile: every tile already has the live release's bytes")
    elif args.command == "compose":
        print(
            json.dumps(
                tile_compose.compose(
                    args.repo, commit, args.published, args.base_dir, args.base_key
                )
            )
        )
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
