"""The city's lidar point cloud by 500 m tile: the one place that finds, fetches and decodes a LAZ tile.

Four reductions read the point cloud (ground rasters for `street`, vegetation returns for `green`, object returns
and overhead wires for `objects`). Each keeps its own product per tile in the shared source cache; what they share
is here:

- `laz_path(name)`: the tile's file. A read-only mirror first (`$TURKU3D_DATA/raw/laz/pistepilvi`, on
  osgallery-dev a link to the whole survey), else the shared cache, else fetched once from the city. None where the
  survey has no tile (the city's edge, open sea), remembered as `<name>.absent`.
- `read(path)`: the decoded tile. Inside `keeping()` the last tile stays in memory, so the reductions of one tile
  made one after another decode it once.
- `ring(name)`: what the wire detection of the neighbouring tiles needs of this tile, its 60 m edge reduced to
  the returns that detection can use (about a fifth of the edge), stored once. Without it every wire tile decoded
  its eight neighbours whole.
- `prepare(box, ...)`: makes every per-tile product a box needs before the kinds ask, in worker processes that
  end with their memory, a bounded number at a time on the whole machine (`TURKU3D_LIDAR_SLOTS`), and never the
  same tile twice at once. A decode with its reductions holds 2 to 3 GB for a dense tile: that is what the slots
  bound. Kinds still make a product on first use when this stage did not; the result is the same.

A tile is named by its north-west corner, as the city names it.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from ... import config
from ..store import data_root

TILE = 500.0
RING = 60.0  # metres of a tile's edge its neighbours' wire detection reads (objects.wires.SEARCH)
RING_VERSION = "1"
GROUND, NOISE = 2, 7
WIRE_CELL = 2.0  # the wire detection's ground grid
WIRE_LOW = 0.8  # metres above that grid below which it uses no return


def sources_root() -> Path:
    return data_root() / "sources"


def tile_name(e0: float, n0: float) -> str:
    """The tile whose south-west corner is (e0, n0)."""
    return f"{int(e0)}_{int(n0 + TILE)}"


def tile_corner(name: str) -> tuple[float, float]:
    """South-west corner of a tile."""
    east, north = name.split("_")
    return float(east), float(north) - TILE


def names(box: tuple[float, float, float, float], margin: float = 0.0) -> list[str]:
    """The tiles a survey box (e0, n0, e1, n1) touches, grown by a margin."""
    e0, n0, e1, n1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
    return [tile_name(te * TILE, tn * TILE)
            for te in range(int(np.floor(e0 / TILE)), int(np.floor((e1 - 1e-6) / TILE)) + 1)
            for tn in range(int(np.floor(n0 / TILE)), int(np.floor((n1 - 1e-6) / TILE)) + 1)]


def neighbours(name: str) -> list[str]:
    e0, n0 = tile_corner(name)
    return [tile_name(e0 + de * TILE, n0 + dn * TILE) for de in (-1, 0, 1) for dn in (-1, 0, 1) if de or dn]


# -------------------------------------------------------------------- the file


def _cached(name: str) -> Path:
    return sources_root() / "laz" / "pistepilvi" / f"{name}.laz"


def absent(name: str) -> bool:
    """Whether the city's server has answered that the survey lacks this tile."""
    cached = _cached(name)
    return any(cached.with_suffix(suffix).exists() for suffix in (".absent", ".unsurveyed", ".missing"))


def laz_path(name: str, fetch: bool = True) -> Path | None:
    """The tile's LAZ file, fetched when it is nowhere on this machine; None where the survey has none (or, with
    `fetch` off, where this machine has none)."""
    cached = _cached(name)
    for path in (config.raw_dir("laz") / "pistepilvi" / f"{name}.laz", config.raw_dir("laz-3x3") / f"{name}.laz", cached):
        if path.exists():
            return path
    if not fetch or absent(name):
        return None
    cached.parent.mkdir(parents=True, exist_ok=True)
    part = cached.with_name(f"{cached.name}.{os.getpid()}.part")
    url = config.LAZ_URL.format(dataset="pistepilvi", name=name)
    done = subprocess.run(["curl", "-L", "--fail", "--silent", "--show-error", "--max-time", "900", "-o", str(part), url],
                          capture_output=True, text=True, timeout=930)
    if done.returncode:
        part.unlink(missing_ok=True)
        if "returned error: 404" in done.stderr:  # curl exits 22 or 56 for it, by version
            cached.with_suffix(".absent").write_text(f"{url}\n404\n")
            return None
        raise OSError(f"lidar tile {name}: {done.stderr.strip()}")
    part.replace(cached)
    return cached


# -------------------------------------------------------------------- the decode

_keep = False
_kept: tuple[Path, object] | None = None


def read(path: Path):
    """The decoded tile (laspy's LasData)."""
    global _kept
    import laspy

    if _kept is not None and _kept[0] == path:
        return _kept[1]
    _kept = None
    las = laspy.read(path)
    if _keep:
        _kept = (path, las)
    return las


@contextmanager
def keeping():
    """While the block runs, the tile decoded last stays in memory for the next reduction of the same tile."""
    global _keep, _kept
    before, _keep = _keep, True
    try:
        yield
    finally:
        _keep = before
        if not _keep:
            _kept = None


# -------------------------------------------------------------------- what the wire detection reads


def wire_returns(e, n, h, cls) -> np.ndarray:
    """Which returns the wire detection (objects.wires.detect over turku3d.wire_points) can use, as a mask.

    The detection reads ground returns only as the lowest per 2 m cell, and every other return only from 0.8 m
    above that. Kept: the lowest ground return of each cell; every return of a cell without ground (its height
    depends on how the gap is filled around it); and what stands 0.8 m or more above its cell's ground, noise
    (class 7) left out: the detection uses it nowhere. The cells are the detection's own: east from an even metre,
    north downwards from an even metre, so a return on a cell edge falls where the detection puts it.
    """
    col = np.floor(e / WIRE_CELL).astype(np.int64)
    row = np.floor(-n / WIRE_CELL).astype(np.int64)
    col -= col.min()
    row -= row.min()
    width = int(col.max()) + 1
    cell = row * width + col
    ground = cls == GROUND
    lowest = np.full(int(cell.max()) + 1, np.inf)
    np.minimum.at(lowest, cell[ground], h[ground])
    base = lowest[cell]
    # A return exactly on an even-metre northing lies on the edge of its cell, where a crop may cut the cell off
    # from its other returns: all of those stay (one return in two thousand).
    keep = ~np.isfinite(base) | ((cls != NOISE) & ((h - base >= WIRE_LOW) | (np.mod(n, WIRE_CELL) == 0)))
    # One lowest ground return per cell (the first of equals).
    at = np.flatnonzero(ground)
    order = at[np.lexsort((h[at], cell[at]))]
    first = np.r_[True, cell[order][1:] != cell[order][:-1]] if len(order) else np.zeros(0, bool)
    keep[order[first]] = True
    return keep


def tile_wire_returns(name: str, box: tuple[float, float, float, float] | None = None, fetch: bool = True) -> np.ndarray:
    """Rows (E, N, H, class) of a tile's wire-detection returns inside a survey box, from its LAZ file."""
    path = laz_path(name, fetch=fetch)
    if path is None:
        return np.zeros((0, 4))
    las = read(path)
    e, n, h, cls = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z), np.asarray(las.classification)
    if box is not None:
        inside = (e >= box[0]) & (e < box[2]) & (n >= box[1]) & (n < box[3])
        e, n, h, cls = e[inside], n[inside], h[inside], cls[inside]
    if not len(e):
        return np.zeros((0, 4))
    keep = wire_returns(e, n, h, cls)
    return np.column_stack([e[keep], n[keep], h[keep], cls[keep]])


def ring_path(name: str) -> Path:
    return sources_root() / "laz-rings" / RING_VERSION / f"{name}.npz"


def ring(name: str, fetch: bool = False) -> np.ndarray | None:
    """Rows (E, N, H, class) of the wire-detection returns within `RING` metres of a tile's border, made from the
    tile on first use and stored. None where this machine has no such tile (`fetch` off) or the survey has none.

    Positions are stored as the file's own integers, so a row is exactly what a decode of the tile gives.
    """
    path = ring_path(name)
    if path.exists():
        with np.load(path) as data:
            scale, offset = data["scale"], data["offset"]
            return np.column_stack([data["x"] * scale[0] + offset[0], data["y"] * scale[1] + offset[1],
                                    data["z"] * scale[2] + offset[2], data["cls"]])
    source = laz_path(name, fetch=fetch)
    if source is None:
        return None
    las = read(source)
    e0, n0 = tile_corner(name)
    e, n, h, cls = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z), np.asarray(las.classification)
    # The filter sees the whole tile, so that a cell's ground is the same whichever neighbour asks.
    keep = wire_returns(e, n, h, cls)
    keep &= (e < e0 + RING) | (e >= e0 + TILE - RING) | (n < n0 + RING) | (n >= n0 + TILE - RING)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.stem}.{os.getpid()}.part.npz")
    np.savez_compressed(part, x=np.asarray(las.X)[keep], y=np.asarray(las.Y)[keep], z=np.asarray(las.Z)[keep],
                        cls=cls[keep].astype(np.uint8), scale=np.asarray(las.header.scales), offset=np.asarray(las.header.offsets))
    part.replace(path)
    return np.column_stack([e[keep], n[keep], h[keep], cls[keep]])


# -------------------------------------------------------------------- every product of a box, made ahead


def slots() -> int:
    """How many tiles are decoded and reduced at once on this machine, over all builds."""
    return max(1, int(os.environ.get("TURKU3D_LIDAR_SLOTS") or min(4, (os.cpu_count() or 4) // 4)))


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def slot():
    """One of the machine's decode slots, waited for."""
    root = sources_root() / ".locks"
    root.mkdir(parents=True, exist_ok=True)
    count = slots()
    while True:
        for index in range(count):
            handle = open(root / f"slot-{index}", "w")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()
            return
        time.sleep(0.5 + 0.5 * (os.getpid() % 7) / 7)


def _products() -> dict:
    """The per-tile products: label -> (exists(name), make(name, sources), phase). Phase 1 are the reductions of a
    tile's own returns, which share one decode; phase 2 the detections, which read reductions and rings of the
    tile and its neighbours."""
    from ..green import lidar as green
    from ..objects import lidar as objects
    from ..objects import spans, wires
    from ..street import lidar as street

    def street_tile(name, sources):
        street.tile(name)
        street.tile.cache_clear()

    def green_tile(name, sources):
        green.tile(name)
        green.tile.cache_clear()

    return {
        "ground rasters": (street.tile_done, street_tile, 1),
        "vegetation": (green.tile_done, green_tile, 1),
        "objects": (objects.tile_done, lambda name, sources: objects.ObjectLidar(log=sources.log).tile(*tile_corner(name)), 1),
        "ring": (lambda name: ring_path(name).exists() or absent(name), lambda name, sources: ring(name, fetch=True), 1),
        "wires": (wires.tile_done, lambda name, sources: wires.tile(name, sources.log), 2),
        "spans": (spans.tile_done, lambda name, sources: spans.tile(sources, *tile_corner(name)), 2),
    }


def _work(job: tuple[str, tuple[str, ...]]) -> tuple[str, list[str], float]:
    """Makes the named products of one tile in a worker process: (name, what was made, seconds)."""
    name, labels = job
    from . import Sources

    os.environ.setdefault("RAYON_NUM_THREADS", "4")  # the decode's threads; several workers share the machine
    sources = Sources(log=lambda *_: None)
    products = _products()
    started = time.perf_counter()
    made = []
    # One process at a time per tile and phase, on the whole machine: a second build waits here, then finds the
    # products made.
    with _locked(sources_root() / ".locks" / f"{name}-{products[labels[0]][2]}"):
        todo = [label for label in labels if not products[label][0](name)]
        if todo:
            with slot(), keeping():
                for label in todo:
                    products[label][1](name, sources)
                    made.append(label)
    return name, made, time.perf_counter() - started


def missing(box: tuple[float, float, float, float], margin: float = RING) -> list[dict[str, tuple[str, ...]]]:
    """What the tiles of a box still lack, as two phases of {tile: products to make}. A tile whose wires are
    missing needs the rings of its neighbours: of those in the box, and of the others when this machine has
    them (the detection takes a neighbour's edge only when it is there)."""
    products = _products()
    own = names(box, margin)
    lacks = {name: [label for label, (done, _, _) in products.items() if label != "ring" and not done(name)] for name in own}
    rings = {near for name in own if "wires" in lacks[name] for near in neighbours(name)}
    rings = {name for name in rings if not products["ring"][0](name) and (name in lacks or laz_path(name, fetch=False) is not None)}
    first = {name: tuple(label for label in lacks.get(name, []) if products[label][2] == 1) + (("ring",) if name in rings else ())
             for name in sorted(set(own) | rings)}
    second = {name: tuple(label for label in lacks[name] if products[label][2] == 2) for name in own}
    return [{name: labels for name, labels in phase.items() if labels} for phase in (first, second)]


def prepare(box: tuple[float, float, float, float], log=print, jobs: int | None = None, margin: float = RING) -> dict:
    """Makes every per-tile lidar product a survey box needs that this machine lacks. Returns {tiles, made, s}."""
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    started = time.perf_counter()
    phases = missing(box, margin)
    tiles = len(set(phases[0]) | set(phases[1]))
    made = 0
    if tiles:
        workers = max(1, min(jobs or int(os.environ.get("TURKU3D_JOBS") or 1), slots(), max(len(phase) for phase in phases)))
        log(f"  lidar: {len(phases[0])} tiles to reduce, {len(phases[1])} to search for wires and spans, in {workers} process(es) "
            f"of this machine's {slots()} slots")
        # Fresh processes: they start without this one's memory and give theirs back when the stage ends.
        with ProcessPoolExecutor(workers, mp_context=get_context("spawn")) as pool:
            for phase in phases:
                for name, labels, seconds in pool.map(_work, list(phase.items())):
                    if labels:
                        made += 1
                        log(f"  lidar tile {name}: {', '.join(labels)} in {seconds:.1f} s")
    return {"tiles": tiles, "made": made, "s": round(time.perf_counter() - started, 2)}
