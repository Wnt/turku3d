"""Content-addressed tile releases for the localhost game (tools/dev/localhost/README.md).

Layout (owned by tool-localhost-hotswap):

    $TURKU3D_DATA/tile-store/<key>/            one complete tile dist tree (tileset.json, manifest.json, visual/,
                                               collision/, ground/, roads/, vehicles/, addresses/) and STORE.json;
                                               immutable once renamed into place, APFS clones of the build output
    $TURKU3D_DATA/tile-store/<key>/STORE.json  {"key", "commit", "source", "time", "tiles"}
    $TURKU3D_DATA/tile-store/index.jsonl       the same record per entry, in the order entries were added

The key of a commit hashes what its tile build reads: the git blob ids of the refresh and tile-build inputs
(live_cache.INPUTS) at that commit and the data root's parts (landing_class.data_parts: facade approvals, the
playable tile list, the published layers' JSON manifests). The integrator's tile-build stage puts its output under
the key (source "integrator"); the localhost service's quick build (tile-build on the published layers, no refresh)
puts its output under sha256(key + ":quick") (source "localhost"), so it never shadows the integrator's build of the
same inputs. Once the integrator publishes a commit's refresh output, the data root's layers change with it and the
commit's key moves to the live release's, which the service then serves directly.
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
PATTERNS = tuple(dict.fromkeys(live_cache.INPUTS["refresh"] + live_cache.INPUTS["tile-build"]))


def root(published):
    return Path(published) / "tile-store"


def key(repo, commit, published):
    """Content key of the tile build of commit against the data root published."""
    h = hashlib.sha256(b"turku3d-tile-store v1\0")
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--full-tree", commit],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for line in tree.splitlines():
        meta, name = line.split("\t", 1)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in PATTERNS):
            h.update(f"{name}\0{meta.split()[2]}\n".encode())
    h.update(json.dumps(landing_class.data_parts(published), sort_keys=True).encode())
    return h.hexdigest()


def quick_key(full_key):
    return hashlib.sha256(f"{full_key}:quick".encode()).hexdigest()


def entry(published, store_key):
    """The STORE.json record of a complete entry, or None."""
    record = root(published) / store_key / "STORE.json"
    try:
        return json.loads(record.read_text())
    except (OSError, ValueError):
        return None


def put(published, tiles, store_key, commit, source, built=None):
    """Clone the tile dist `tiles` into the store under store_key; an existing entry is kept."""
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
        "tiles": list(built) if built else None,
    }
    (temp / "STORE.json").write_text(json.dumps(record) + "\n")
    try:
        temp.rename(target)
    except OSError:
        shutil.rmtree(temp, ignore_errors=True)
        return target if target.exists() else None
    with (store / "index.jsonl").open("a") as index:
        index.write(json.dumps(record) + "\n")
    prune(published)
    return target


def prune(published, keep=KEEP):
    """Remove all but the newest `keep` entries."""
    store = root(published)
    entries = sorted(
        (path for path in store.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.stat().st_mtime,
    )
    for path in entries[:-keep]:
        shutil.rmtree(path, ignore_errors=True)


def resolve(repo, commit, published):
    """{key, quick_key, integrator, localhost}: the entry directories present for commit, or null."""
    full = key(repo, commit, published)
    quick = quick_key(full)
    present = lambda k: str(root(published) / k) if entry(published, k) else None  # noqa: E731
    return {
        "key": full,
        "quick_key": quick,
        "integrator": present(full),
        "localhost": present(quick),
    }


def quick_build(repo, commit, published, work, tiles, base):
    """Tile-build `tiles` of the checkout repo on the published layers into work; put it as the quick entry."""
    import live_build

    full = key(repo, commit, published)
    work = Path(work)
    shutil.rmtree(work, ignore_errors=True)
    live_build.seed(work, Path(published))
    # Start from the newest tiles the service streams, so unaffected tiles keep their bytes.
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
    return put(published, work / "dist/tiles", quick_key(full), commit, "localhost", tiles)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("key", "resolve", "quick-build", "put"):
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
    args = parser.parse_args(argv)
    commit = subprocess.run(
        ["git", "rev-parse", args.commit], cwd=args.repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    if args.command == "key":
        print(key(args.repo, commit, args.published))
    elif args.command == "resolve":
        print(json.dumps(resolve(args.repo, commit, args.published)))
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
    else:
        print(quick_build(args.repo, commit, args.published, args.work, args.tiles, args.base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
