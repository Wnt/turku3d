"""Per-tile layer builds: each listed L0 tile reads its own input area and is cached by key.

A tile-following producer splits its work into one build per L0 tile. The build of a tile
reads the tile's inputs (`wfs(tile, name)`, `osm(tile)`: `config.source_area`, which is
work-3x3 while that holds the tile's input area, else the tile's own `tile-<id>` area) and
owns exactly the layer files of that tile: `<tile>.vis.glb` and the `.col.npz` of its
sixteen collision tiles. Objects belong to the tile their position lies in, so separately
built tiles never write the same file. Its records (the objects it placed, the road ways it
clipped) go to `<tile>.records.json` beside the layer files.

The layer MANIFEST's `tile_builds` keeps each tile's key and artifact hashes. A tile whose key
is unchanged and whose files still match is not rebuilt, so adding a tile builds that tile and
the tiles whose keys the addition changes (for example placement neighbours whose clearance
envelope reaches into it). A producer's `key(tile)` must name everything its build reads.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from turku3d import config as C
from turku3d.package.manifest import _atomic_write

RECORDS = ".records.json"


def wfs(tile, name: str) -> Path:
    """A city WFS layer of the tile's input area (may be missing for optional layers)."""
    return C.raw_dir("wfs") / C.source_area(_tile(tile)) / f"{name}.parquet"


def osm(tile) -> Path:
    """The tile's dated OSM clip; the latest snapshot when several exist."""
    area = C.source_area(_tile(tile))
    found = sorted((C.raw_dir("osm") / area).glob("*.parquet"))
    if not found:
        raise FileNotFoundError(f"OSM GeoParquet missing for {area}; run make tile-add")
    return found[-1]


def _tile(tile) -> C.VisualTile:
    return C.VisualTile.parse(tile) if isinstance(tile, str) else tile


def fingerprints(paths: dict[str, Path]) -> dict[str, dict]:
    """Content hashes with one memo and lock per source file, shared across farm revisions.

    A large lidar read never holds a city-wide index lock. File identity only memoizes
    hashing: stage keys still use the SHA-256 of the actual bytes.
    """
    shared = os.environ.get("TURKU3D_SHARED_CACHE")
    directory = (Path(shared) if shared else C.cache_dir("inputs")) / "fingerprints"
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    for key, source in paths.items():
        path = source.resolve()
        name = hashlib.sha256(str(path).encode()).hexdigest()
        cache = directory / (name + ".json")

        def identity():
            stat = path.stat()
            return dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                        ctime_ns=stat.st_ctime_ns, inode=stat.st_ino)

        def read():
            try:
                return json.loads(cache.read_text())
            except (FileNotFoundError, ValueError):
                return {}

        def matches(value, state):
            return "sha256" in value and all(value.get(k) == v for k, v in state.items())

        state, value = identity(), read()
        if not matches(value, state):
            with cache.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                state, value = identity(), read()
                if not matches(value, state):
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if identity() != state:
                        raise RuntimeError(f"source changed while hashing: {path}")
                    value = {**state, "sha256": digest}
                    temporary = cache.with_name(f"{name}.{os.getpid()}.tmp")
                    temporary.write_text(json.dumps(value) + "\n")
                    temporary.replace(cache)
        result[key] = value
    return result


def identities(paths: dict[str, Path]) -> dict[str, str | None]:
    """Content hashes of named inputs; a missing optional input is None."""
    present = {k: p for k, p in paths.items() if p.is_file()}
    found = fingerprints(present)
    return {k: (found[k]["sha256"] if k in found else None) for k in sorted(paths)}


def code_hash(*roots: Path) -> str:
    """Identity of Python sources (files, or every .py below a directory)."""
    files = sorted({p for root in roots for p in (root.rglob("*.py") if root.is_dir() else [root])})
    h = hashlib.sha256()
    for path in files:
        h.update(path.name.encode() + path.read_bytes())
    return h.hexdigest()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def owned(tile, name: str) -> bool:
    """True for the layer files a tile's build may write: its visual and its collision tiles."""
    tile = _tile(tile)
    if name == f"{tile.id}.vis.glb" or name == f"{tile.id}{RECORDS}":
        return True
    if name.endswith(".col.npz"):
        return C.CollisionTile.parse(name.removesuffix(".col.npz")).visual_parent() == tile
    return False


@dataclass(frozen=True)
class Producer:
    """A per-tile layer producer.

    `key(tile)` returns JSON-able identities of everything the tile's build reads.
    `build(tile, staging)` writes the tile's files into the empty staging directory and
    returns `{"records": [...], "metrics": {...}}`. `combine(directory, tiles)` writes the
    layer-wide side files from every listed tile's `{"records", "metrics"}` (in tile order)
    and returns extra manifest params. All three must be module-level functions (process pools pickle
    them by reference)."""

    layer: str
    code: Callable[[], str]
    key: Callable[[C.VisualTile], dict]
    build: Callable[[C.VisualTile, Path], dict]
    combine: Callable[[Path, dict], dict] | None = None
    params: dict = field(default_factory=dict)

    def tile_key(self, tile) -> str:
        tile = _tile(tile)
        return digest({"tile": tile.id, "code": self.code(), "inputs": self.key(tile)})


def _previous(directory: Path) -> dict:
    path = directory / "MANIFEST.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text()).get("tile_builds", {})


def _intact(directory: Path, record: dict) -> bool:
    return all(
        (directory / name).is_file() and sha(directory / name) == expected
        for name, expected in record["artifacts"].items()
    )


def stale(directory: Path, producer: Producer, tiles) -> list[str]:
    """The listed tiles whose key changed or whose files are missing; also the cache check."""
    directory = Path(directory)
    previous = _previous(directory)
    result = []
    for tile in map(_tile, tiles):
        record = previous.get(tile.id)
        if (
            record is None
            or record.get("key") != producer.tile_key(tile)
            or not _intact(directory, record)
        ):
            result.append(tile.id)
    listed = {_tile(t).id for t in tiles}
    if set(previous) - listed:
        result.append("unlisted")  # A tile left the list: its files must go.
    return result


def _build_one(job):
    producer, tile_id, key, staging = job
    started = time.monotonic()
    tile = C.VisualTile.parse(tile_id)
    staging = Path(staging)
    result = producer.build(tile, staging)
    written = sorted(p.name for p in staging.iterdir())
    foreign = [name for name in written if not owned(tile, name)]
    if foreign:
        raise ValueError(f"{producer.layer} {tile_id}: wrote files it does not own: {foreign}")
    records = {
        "tile": tile_id,
        "key": key,
        "records": result.get("records", []),
        "metrics": result.get("metrics", {}),
        "seconds": round(time.monotonic() - started, 3),
    }
    _atomic_write(staging / f"{tile_id}{RECORDS}", (json.dumps(records, indent=1) + "\n").encode())
    return {p.name: sha(p) for p in sorted(staging.iterdir())}, records["seconds"]


def run(directory: Path, producer: Producer, tiles, jobs: int | None = None) -> dict:
    """Build the stale listed tiles, keep the others, publish the layer manifest last."""
    from turku3d.layers import write_manifest

    directory = Path(directory)
    tiles = sorted({_tile(t) for t in tiles})
    if any(t.level != 0 for t in tiles):
        raise ValueError(f"{producer.layer}: per-tile layers are built per L0 tile")
    started = time.monotonic()
    previous = _previous(directory)
    keys = {t.id: producer.tile_key(t) for t in tiles}
    builds = {
        t.id: previous[t.id]
        for t in tiles
        if t.id in previous
        and previous[t.id].get("key") == keys[t.id]
        and _intact(directory, previous[t.id])
    }
    todo = [t.id for t in tiles if t.id not in builds]
    directory.mkdir(parents=True, exist_ok=True)
    seconds = {}
    with tempfile.TemporaryDirectory(prefix=f".{producer.layer}-", dir=directory.parent) as temp:
        jobs_ = [(producer, tid, keys[tid], str(Path(temp) / tid)) for tid in todo]
        for _, _, _, staging in jobs_:
            Path(staging).mkdir()
        workers = min(len(jobs_), max(1, jobs or int(os.environ.get("TURKU3D_JOBS", "1"))))
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_build_one, jobs_))
        else:
            results = [_build_one(job) for job in jobs_]
        for (_, tid, key, staging), (artifacts, elapsed) in zip(jobs_, results, strict=True):
            for name in artifacts:
                target = directory / name
                if not target.is_file() or sha(target) != artifacts[name]:
                    _atomic_write(target, (Path(staging) / name).read_bytes())
            builds[tid] = {"key": key, "artifacts": artifacts}
            seconds[tid] = elapsed
    keep = {name for record in builds.values() for name in record["artifacts"]}
    for path in directory.iterdir():
        if path.name.endswith((".col.npz", ".vis.glb", RECORDS)) and path.name not in keep:
            path.unlink()
    entries = {t.id: json.loads((directory / f"{t.id}{RECORDS}").read_text()) for t in tiles}
    params = dict(producer.params)
    if producer.combine:
        params.update(producer.combine(directory, entries) or {})
    layer_files = sorted(n for n in keep if not n.endswith(RECORDS))
    manifest = write_manifest(
        directory,
        inputs={f"{t.id}{RECORDS}": builds[t.id]["artifacts"][f"{t.id}{RECORDS}"] for t in tiles},
        params={**params, "seams": "objects", "visual_tiles": [t.id for t in tiles]},
        code=producer.code(),
        tiles=layer_files,
    )
    manifest["tile_builds"] = {t.id: builds[t.id] for t in tiles}
    _atomic_write(
        directory / "MANIFEST.json",
        (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode(),
    )
    return {
        "layer": producer.layer,
        "built": todo,
        "cached": sorted(builds.keys() - set(todo)),
        "tile_seconds": seconds,
        "seconds": round(time.monotonic() - started, 3),
    }
