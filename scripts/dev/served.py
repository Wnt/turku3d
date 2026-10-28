"""The tile set the localhost game serves for a commit, and its provenance in a release record.

Jonni's decision (2026-10-27 22:55): live and Pages publish what localhost already serves. A code batch never
rebuilds tiles: the integrator builds main's game bundle and ships it with `tiles(repo, commit, published)`, the
tile store entry for the commit's tile key (tile_store.key, the same entry tools/dev/localhost/tiles.mjs streams
first) when one exists, else the live release's tiles.

A release record (live-build.json) names where its tiles came from:

    tiles_from     {"source": "live" | "store" | "build", "commit", "release" | "key"}: the tile set the release
                   ships and the commit whose tile build produced it
    tile_commits   {tile: commit}: per shipped L0 tile, the commit whose tile build produced its bytes
    stale_by_code  the shipped tiles whose builder's tile code (refresh and tile-build inputs) differs from the
                   tile code of the release commit; `make tiles-refresh-stale` rebuilds them

`release(published, main_repo)` is what `publish-pages --from-served` ships: the live release when it already is
main's bundle with the served tiles, else main's production bundle built in the `pages-served` sandbox composed with
the served tiles (compose).
"""

from __future__ import annotations

import fnmatch
import functools
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import sandbox as s
import tile_store
from landing_class import C, released_tiles

# The sandbox publish-pages --from-served builds main's production bundle in.
PAGES_SANDBOX = "pages-served"


def record(release):
    return json.loads((Path(release) / "live-build.json").read_text())


def builder(entry):
    """The commit whose tile build produced a release's tiles (older records: the verified commit)."""
    return (entry.get("tiles_from") or {}).get("commit") or entry.get(
        "verified_commit", entry["commit"]
    )


def release_tiles(release):
    """{tile: commit} of a published release's tiles."""
    entry = record(release)
    if "tile_commits" in entry:
        return dict(entry["tile_commits"])
    return {tile: builder(entry) for tile in released_tiles(release)}


@functools.lru_cache(maxsize=256)
def _tile_code(repo, commit):
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--full-tree", commit],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    h = hashlib.sha256(b"turku3d-tile-code v1\0")
    for line in tree.splitlines():
        meta, name = line.split("\t", 1)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in tile_store.PATTERNS):
            h.update(f"{name}\0{meta.split()[2]}\n".encode())
    return h.hexdigest()


def tile_code(repo, commit):
    """Hash of the tile build's code (tile_store.PATTERNS blobs) at commit; None for an unknown commit."""
    try:
        return _tile_code(str(repo), commit)
    except subprocess.CalledProcessError:
        return None


def stale_by_code(repo, commit, tile_commits):
    """The tiles whose builder's tile code is not commit's (an unknown builder counts as stale)."""
    current = tile_code(repo, commit)
    return sorted(
        tile
        for tile, built in tile_commits.items()
        if built != commit and tile_code(repo, built) != current
    )


def tiles(repo, commit, published):
    """{"dir", "source", "commit", "tile_commits", "facades_bound", "release" | "key"}: what localhost serves.

    A store entry ships only when it records its facade count (entries put before that field are skipped)."""
    published = Path(published)
    live = (published / "live").resolve()
    shipped = release_tiles(live)
    key = tile_store.key(repo, commit, published)
    entry = tile_store.entry(published, key)
    if entry and entry.get("facades_bound") is not None:
        built = entry.get("tiles") or list(shipped)
        manifest = json.loads((tile_store.root(published) / key / "manifest.json").read_text())
        playable = [tile for tile in manifest["visual"] if tile in shipped or tile in built]
        return {
            "dir": str(tile_store.root(published) / key),
            "source": "store",
            "key": key,
            "commit": entry["commit"],
            "tile_commits": {
                tile: entry["commit"] if tile in built else shipped[tile]
                for tile in sorted(playable)
            },
            "facades_bound": entry["facades_bound"],
        }
    return {
        "dir": str(live / "tiles"),
        "source": "live",
        "release": live.name,
        "commit": builder(record(live)),
        "tile_commits": shipped,
        "facades_bound": record(live)["facades_bound"],
    }


def built(commit, previous_release, tiles, rebuilt):
    """The shipped tile set of a data build of commit (the shape of tiles()): the tiles it rebuilt
    (and tiles the previous release did not carry) are commit's, the rest keep the previous
    release's."""
    previous = release_tiles(previous_release) if previous_release else {}
    return {
        "source": "build",
        "commit": commit,
        "tile_commits": {
            tile: commit if tile in rebuilt or tile not in previous else previous[tile]
            for tile in tiles
        },
    }


def provenance(repo, commit, served):
    """The release record fields of a release of commit that ships the tile set `served`
    (tiles() or built()); stale_by_code is measured against commit's tile code."""
    origin = {"source": served["source"], "commit": served["commit"]}
    origin.update({name: served[name] for name in ("release", "key") if name in served})
    return {
        "tiles_from": origin,
        "tile_commits": served["tile_commits"],
        "stale_by_code": stale_by_code(repo, commit, served["tile_commits"]),
    }


def compose(target, game, shipped, published, repo, commit):
    """Write a release dir at target: the game bundle `game`, the tile set `shipped` (tiles()),
    the live layers, the facade assembly that tile set binds and live-build.json with its
    provenance."""
    published, target = Path(published), Path(target)
    from live_build import _clone

    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    live = (published / "live").resolve()
    _clone(Path(game), target / "game")
    _clone(Path(game).parent / "check-game", target / "check-game")
    _clone(Path(shipped["dir"]), target / "tiles")
    (target / "tiles/STORE.json").unlink(missing_ok=True)
    # The layers let the Pages region measure a tile set that predates its quality ledger.
    _clone(live / "layers", target / "layers")
    if shipped["source"] == "live":
        _clone(live / "textures/assembly", target / "textures/assembly")
    else:
        approvals = published / "facade-approvals/assembly"
        with s.lock(approvals / ".export.lock"):
            shutil.copytree(approvals, target / "textures/assembly")
    entry = {
        "commit": commit,
        "tiles": sorted(shipped["tile_commits"]),
        "facades_bound": shipped["facades_bound"],
        **provenance(repo, commit, shipped),
    }
    (target / "live-build.json").write_text(json.dumps(entry, indent=2) + "\n")
    return target


def release(published, main_repo):
    """The release dir of main's production bundle with the tiles localhost serves."""
    import integrator

    published = Path(published)
    head = s.git("rev-parse", "main", cwd=main_repo)
    shipped = tiles(main_repo, head, published)
    live = (published / "live").resolve()
    if record(live)["commit"] == head and shipped["source"] == "live":
        return live
    repo = integrator.checkout(head, PAGES_SANDBOX, published)
    env = integrator.environment(repo, published)
    built = s.run_group(["make", "game-build"], cwd=repo, env={**env, "VITE_PUBLIC_RELEASE": "1"})
    if built.returncode:
        raise ValueError(f"game-build of {head[:12]} failed: {s.failure_detail(built.stdout)}")
    work = Path(env["TURKU3D_WORK"])
    return compose(work / "pages-release", work / "build/game", shipped, published, repo, head)


def carry_game(release, work):
    """Replace the game bundle in work with release's (a data build shipping a newer live game)."""
    from live_build import _replace

    _replace(release / "game", work / "build/game")
    _replace(
        release / ("check-game" if (release / "check-game").is_dir() else "game"),
        work / "build/check-game",
    )


def ship(repo, work, published):
    """Put the tile set localhost serves for repo's HEAD (tiles()) into work with the live
    layers, textures and game; returns that set. Live tiles keep the live facade assembly; a
    store entry was built against the current data root, so it takes the approvals."""
    from live_build import _replace, approve

    commit = s.git("rev-parse", "HEAD", cwd=repo)
    shipped = tiles(repo, commit, published)
    live = (published / "live").resolve()
    for source, relative in (
        (live / "layers", "build/layers"),
        (live / "textures", "textures"),
        (live / "game", "build/game"),
        (live / ("check-game" if (live / "check-game").is_dir() else "game"), "build/check-game"),
        (Path(shipped["dir"]), "dist/tiles"),
    ):
        _replace(source, work / relative)
    (work / "dist/tiles/STORE.json").unlink(missing_ok=True)
    (work / C.PLAYABLE_TILES_FILE).unlink(missing_ok=True)
    if shipped["source"] != "live":
        approve(work, published)
    return shipped
