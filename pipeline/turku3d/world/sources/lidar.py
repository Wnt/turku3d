"""The city's lidar point cloud by 500 m tile: the one place that finds, fetches and decodes a LAZ tile.

Four reductions read the point cloud (ground rasters for `street`, vegetation returns for `green`, object returns
and overhead wires for `objects`). Each keeps its own product per tile in the shared source cache; what they share
is here:

- `laz_path(name)`: the tile's file. A read-only mirror first (`$TURKU3D_DATA/raw/laz-survey`, on osgallery-dev
  a link to the whole survey; `raw/laz/pistepilvi`, the pilot tiles), else the shared cache, else fetched once
  from the city. None where the survey has no tile (the city's edge, open sea), remembered as `<name>.absent`.
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
RING_VERSION = "2"
GROUND, NOISE = 2, 7
WIRE_CELL = 2.0  # the wire detection's ground grid
WIRE_LOW = 0.8  # metres above that grid below which it uses no return


def sources_root() -> Path:
    return data_root() / "sources"


def products_root() -> Path:
    """Where the per-tile reductions of the point cloud are kept (ground rasters, vegetation, object returns, rings:
    about 134 MB a tile, 170 GB for the whole city). The shared source cache, unless the machine names another
    disk in `TURKU3D_LIDAR_PRODUCTS` (osgallery-dev: on /bulk, its home disk cannot hold the city). A private data
    root (`TURKU3D_WORLD_DATA`) keeps its own products, so that a build there starts cold."""
    root = os.environ.get("TURKU3D_LIDAR_PRODUCTS")
    return Path(root).expanduser() if root and not os.environ.get("TURKU3D_WORLD_DATA") else sources_root()


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
    mirrors = (config.raw_dir("laz-survey"), config.raw_dir("laz") / "pistepilvi", config.raw_dir("laz-3x3"))
    for path in (*(mirror / f"{name}.laz" for mirror in mirrors), cached):
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


def wire_returns(e, n, h, cls, corner: tuple[float, float]) -> np.ndarray:
    """Which returns of a tile (south-west `corner`) the wire detection (objects.wires.detect over
    turku3d.wire_points) can use, as a mask.

    The detection reads ground returns only as the lowest height per 2 m cell, and every other return only from
    0.8 m above that. Kept: the lowest ground returns of each cell; every return of a cell without ground (its
    height depends on how the gap is filled around it); and what stands 0.8 m or more above its cell's ground, noise
    (class 7) left out: the detection uses it nowhere. The cells are the detection's own: east from an even metre,
    north downwards from an even metre.

    Nothing is dropped where a cell is not whole in what this call sees: in the cells along the tile's border, which
    a neighbouring tile's returns on the border line share, and in the two rows of cells that a neighbour's
    detection cuts at `RING` metres from the border. So the mask of a cell is the same for the whole tile, for
    its ring alone and for any detection's crop.
    """
    col = np.floor(e / WIRE_CELL).astype(np.int64)
    row = np.floor(-n / WIRE_CELL).astype(np.int64)
    col -= col.min()
    row -= row.min()
    cell = row * (int(col.max()) + 1) + col
    del col, row
    ground = cls == GROUND
    lowest = np.full(int(cell.max()) + 1, np.inf)
    np.minimum.at(lowest, cell[ground], h[ground])
    base = lowest[cell]
    del cell
    keep = ~np.isfinite(base) | ((cls != NOISE) & (h - base >= WIRE_LOW)) | (ground & (h == base))
    de, dn = e - corner[0], n - corner[1]
    keep |= (de < WIRE_CELL) | (de >= TILE - WIRE_CELL) | (dn <= WIRE_CELL) | (dn > TILE - WIRE_CELL)
    keep |= ((dn > RING - WIRE_CELL) & (dn <= RING)) | ((dn > TILE - RING - WIRE_CELL) & (dn <= TILE - RING))
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
        if not inside.all():
            e, n, h, cls = e[inside], n[inside], h[inside], cls[inside]
    if not len(e):
        return np.zeros((0, 4))
    keep = wire_returns(e, n, h, cls, tile_corner(name))
    return np.column_stack([e[keep], n[keep], h[keep], cls[keep]])


def ring_path(name: str) -> Path:
    return products_root() / "laz-rings" / RING_VERSION / f"{name}.npz"


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
    e, n = np.asarray(las.x), np.asarray(las.y)
    at = np.flatnonzero((e < e0 + RING) | (e >= e0 + TILE - RING) | (n < n0 + RING) | (n >= n0 + TILE - RING))
    e, n, h, cls = e[at], n[at], np.asarray(las.z)[at], np.asarray(las.classification)[at]
    if len(at):
        keep = wire_returns(e, n, h, cls, (e0, n0))
        at, e, n, h, cls = at[keep], e[keep], n[keep], h[keep], cls[keep]
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.stem}.{os.getpid()}.part.npz")
    _save_fast(part, x=np.asarray(las.X)[at], y=np.asarray(las.Y)[at], z=np.asarray(las.Z)[at], cls=cls.astype(np.uint8),
               scale=np.asarray(las.header.scales), offset=np.asarray(las.header.offsets))
    part.replace(path)
    return np.column_stack([e, n, h, cls])


def _save_fast(path: Path, **arrays) -> None:
    """An .npz with light compression: a third of the time of `np.savez_compressed` for a tenth more bytes."""
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for key, value in arrays.items():
            with archive.open(f"{key}.npy", "w", force_zip64=True) as stream:
                np.lib.format.write_array(stream, np.asanyarray(value), allow_pickle=False)


# -------------------------------------------------------------------- every product of a box, made ahead


def slots() -> int:
    """How many tiles are decoded and reduced at once on this machine, over all builds. A dense tile's wire search
    holds 5 to 7 GB: one slot per 16 GB of memory and per four cores."""
    if os.environ.get("TURKU3D_LIDAR_SLOTS"):
        return max(1, int(os.environ["TURKU3D_LIDAR_SLOTS"]))
    memory_gb = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    return max(1, min((os.cpu_count() or 4) // 4, int(memory_gb // 16)))


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
def slot(spare: int = 0):
    """One of the machine's decode slots, waited for. `spare` leaves that many of them to others: a batch over
    the city takes all but one, so that a location someone waits for still gets its tiles."""
    root = sources_root() / ".locks"
    root.mkdir(parents=True, exist_ok=True)
    count = slots()
    while True:
        for index in range(min(spare, count - 1), count):
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


def _work(job: tuple[str, tuple[str, ...], int]) -> tuple[str, list[str], float, float]:
    """Makes the named products of one tile in a worker process: (name, what was made, seconds, peak MB)."""
    name, labels, spare = job
    import resource
    import sys

    from . import Sources

    os.environ.setdefault("RAYON_NUM_THREADS", "4")  # the decode's threads; several workers share the machine
    sources = Sources(log=lambda *_: None)
    products = _products()
    phase = products[labels[0]][2]
    started = time.perf_counter()
    made = []
    # One process at a time per tile and phase, on the whole machine: a second build waits here, then finds the
    # products made.
    with _locked(sources_root() / ".locks" / f"{name}-{phase}"):
        todo = [label for label in labels if not products[label][0](name)]
        if todo:
            with slot(spare):
                if phase == 1:  # the reductions share the decode; a detection reads it once and lets it go
                    with keeping():
                        for label in todo:
                            products[label][1](name, sources)
                            made.append(label)
                else:
                    for label in todo:
                        products[label][1](name, sources)
                        made.append(label)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**20 if sys.platform == "darwin" else 2**10)
    return name, made, time.perf_counter() - started, peak


def missing(tiles: list[str], also: tuple[str, ...] | list[str] = ()) -> list[dict[str, tuple[str, ...]]]:
    """What tiles still lack, as two phases of {tile: products to make}. A tile whose wires are missing needs the
    rings of its neighbours: of those among the tiles, and of the others when this machine has them (the
    detection takes a neighbour's edge only when it is there). The tiles in `also` get their reductions (the
    first phase) and are not searched: a batch over the city names the neighbours of its tiles there, since the
    span search reads a neighbour's object returns and would otherwise decode that tile on its own."""
    products = _products()
    lacks = {name: [label for label, (done, _, _) in products.items() if label != "ring" and not done(name)] for name in tiles}
    reduce_only = {name: [label for label, (done, _, phase) in products.items() if phase == 1 and not done(name)]
                   for name in also if name not in lacks}
    rings = {near for name in tiles if "wires" in lacks[name] for near in neighbours(name)}
    rings = {name for name in rings if not products["ring"][0](name) and (name in lacks or laz_path(name, fetch=False) is not None)}
    first = {name: tuple(label for label in lacks.get(name, []) if products[label][2] == 1) + (("ring",) if name in rings else ())
             for name in sorted(set(tiles) | rings)}
    first.update({name: tuple(labels) for name, labels in reduce_only.items()})
    second = {name: tuple(label for label in lacks[name] if products[label][2] == 2) for name in tiles}
    return [{name: labels for name, labels in phase.items() if labels} for phase in (first, second)]


def _send(sender, job) -> None:
    import faulthandler

    faulthandler.enable()  # a crash in native code leaves the Python stack in the build's log
    try:
        try:  # when the machine runs out of memory, this process goes first: its tile is made again, a build is not
            Path("/proc/self/oom_score_adj").write_text("500")
        except OSError:
            pass
        sender.send(_work(job))
    except BaseException as error:  # the parent raises it; a traceback object does not travel
        import traceback

        sender.send(RuntimeError(f"lidar tile {job[0]}: {type(error).__name__}: {error}\n{traceback.format_exc()}"))
    finally:
        sender.close()


def _in_process(job):
    """`_work(job)` in a process of its own, which starts without this one's memory and ends with its own. (A
    process pool that replaces its workers after some tasks stopped for good on Python 3.12.)"""
    from multiprocessing import get_context

    context = get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_send, args=(sender, job))
    process.start()
    sender.close()
    try:
        outcome = receiver.recv()
    except EOFError:
        outcome = None
    process.join()
    if outcome is None:
        raise RuntimeError(f"lidar tile {job[0]}: its process ended without a result (exit code {process.exitcode}; "
                           f"killed for memory when negative)")
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def make(tiles: list[str], log=print, jobs: int | None = None, spare: int = 0, also: tuple[str, ...] | list[str] = (),
         keep_going: bool = False, before=None) -> dict:
    """Makes every lidar product the tiles lack on this machine (and the reductions of the tiles in `also`).
    Returns {tiles, made, s, peakMb, failed}: how many tiles lacked something, how many products' worth of tiles
    were made, the largest worker, and the tiles whose process failed.

    At most `jobs` tile processes run at once, in the order of `tiles` (nearest first). There is no barrier
    between the phases: a tile's search starts as soon as its own and its neighbours' reductions and rings have
    ended, and goes before further reductions, so workers never wait for the slowest tile of a round. A tile whose
    reductions failed is not searched (the search would decode it again on its own). A failure stops new work and
    is raised at the end, unless `keep_going`: a batch logs it and goes on, and the tile is made on a later run.
    `before()` is called before each process starts (a batch waits there while the machine is short of room)."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    started = time.perf_counter()
    first, second = missing(tiles, also)
    count = len(set(first) | set(second))
    made, peak, failed = 0, 0.0, {}
    if count:
        rank = {name: k for k, name in enumerate(tiles)}
        reduce = sorted(first, key=lambda name: (rank.get(name, len(rank)), name))
        search = sorted(second, key=lambda name: (rank.get(name, len(rank)), name))
        needs = {name: ({name, *neighbours(name)} & set(first)) for name in search}
        ended: set[str] = set()  # first-phase tiles that ended, made or failed
        workers = max(1, min(jobs or int(os.environ.get("TURKU3D_JOBS") or 1), slots(), count))
        log(f"  lidar: {len(first)} tiles to reduce, {len(second)} to search for wires and spans, {workers} at a time "
            f"(this machine has {slots()} slots)")
        running: dict = {}
        with ThreadPoolExecutor(workers) as pool:  # a thread waits for each tile's process
            while True:
                while len(running) < workers and (keep_going or not failed):
                    ready = next((name for name in search if needs[name] <= ended), None)
                    if ready is not None:
                        search.remove(ready)
                        if ready in failed:
                            continue
                        job = (ready, second[ready], 1)
                    elif reduce:
                        name = reduce.pop(0)
                        job = (name, first[name], 0)
                    else:
                        break
                    if before:
                        before()
                    running[pool.submit(_in_process, (job[0], job[1], spare))] = job
                if not running:
                    break
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for done in finished:
                    name, _, phase = running.pop(done)
                    if phase == 0:
                        ended.add(name)
                    try:
                        name, labels, seconds, worker_peak = done.result()
                    except Exception as error:  # noqa: BLE001 - the tile's process died or raised; the others go on
                        failed[name] = error
                        log(f"  lidar tile {name} FAILED: {str(error).strip().splitlines()[0][:300]}")
                        continue
                    peak = max(peak, worker_peak)
                    if labels:
                        made += 1
                        log(f"  lidar tile {name}: {', '.join(labels)} in {seconds:.1f} s, {worker_peak:.0f} MB")
        if failed and not keep_going:
            raise next(iter(failed.values()))
    return {"tiles": count, "made": made, "s": round(time.perf_counter() - started, 2), "peakMb": round(peak), "failed": sorted(failed)}


def prepare(box: tuple[float, float, float, float], log=print, jobs: int | None = None, margin: float = RING) -> dict:
    """Makes every per-tile lidar product a survey box needs that this machine lacks (`make`)."""
    return make(names(box, margin), log, jobs)


def city_tiles(e: float | None = None, n: float | None = None) -> list[str]:
    """Every tile of the city's survey index, nearest first to a point (the index's middle by default)."""
    import csv

    with open(config.LAZ_TILES_CSV, newline="") as stream:
        rows = [(float(r["xmin"]), float(r["ymax"])) for r in csv.DictReader(stream) if r["dataset"] == "pistepilvi"]
    corners = np.array(rows)
    centre = np.array([e, n]) if e is not None and n is not None else (corners.min(axis=0) + corners.max(axis=0)) / 2
    middle = corners + np.array([TILE / 2, -TILE / 2])
    order = np.argsort(np.hypot(*(middle - centre).T), kind="stable")
    return [f"{int(corners[k, 0])}_{int(corners[k, 1])}" for k in order]
