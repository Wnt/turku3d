"""Keep the tiles' freshness verdicts and served fragments warm, behind the requests.

A tile's first manifest or collision poll after its manifest changed computes its freshness verdict (facade parameter
hash, raw input key) and reads its served fragment: 4-16 s per tile on a loaded host (city.lab Amsterdam :7980,
2026-11-08 15:28, load ~50; a freshly installed prebuilt tile 10.7 s). That happens for every tile after a restart or
code move, for a prebuilt tile installed into the store, and for each tile this service rebuilds, and a fast driver met
a row of them. One background thread warms the tiles on disk whose verdict or fragment is not cached for their current
files, nearest the active demand first (the tiles polled in the last WARM_RECENT_S, else the request journal, else
t0_0_0), then rescans every WARM_SCAN_S for manifests that changed under it.

Its IO runs in the idle class. Its CPU priority stays: a niced thread that holds the GIL while a loaded host's run
queue keeps it off the CPU would stall the request threads with it; it pauses between tiles instead.
"""

import ctypes
import platform
import sys
import threading
import time

from turku3d import config as C

from .serving import fragment_current, retained_fragment

WARM_SCAN_S = 10.0  # s between scans of the tiles on disk for changed manifests (installs, finished builds)
WARM_RECENT_S = 120.0  # a tile polled this recently marks the active demand
WARM_PAUSE_S = 0.05  # s between two tiles, so the request threads get the GIL back


def idle_io() -> bool:
    """Put the calling thread's IO in the idle class (Linux ioprio_set on its thread id). False where unsupported."""
    number = {"x86_64": 251, "aarch64": 30}.get(platform.machine())
    if not sys.platform.startswith("linux") or number is None:
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.syscall(number, 1, threading.get_native_id(), 3 << 13) == 0  # IOPRIO_WHO_PROCESS, CLASS_IDLE
    except (OSError, AttributeError):
        return False


def _ij(tile):
    parsed = C.VisualTile.parse(tile)
    return parsed.i, parsed.j


def demand_anchors(builder) -> list[tuple[int, int]]:
    """Where the players are: tiles polled in the last WARM_RECENT_S, else the request journal, else t0_0_0."""
    now = time.time()
    with builder.lock:
        recent = [t for t, at in builder.requested.items() if now - at <= WARM_RECENT_S]
        journal = list(builder.journal.rows)
    anchors = []
    for tile in recent or journal:
        try:
            anchors.append(_ij(tile))
        except (ValueError, AttributeError):
            continue
    return anchors or [(0, 0)]


def cold_tiles(builder) -> list[str]:
    """The tiles on disk whose verdict or served fragment is not cached for their current files, nearest the active
    demand first (Manhattan tile distance to the nearest anchor, then outward from t0_0_0)."""
    code = builder.code()
    cold = []
    for path in builder.root.glob("t0_*/manifest.json"):
        tile = path.parent.name
        try:
            i, j = _ij(tile)
            st = path.stat()
        except (ValueError, AttributeError, OSError):
            continue
        hit = builder.current.get(tile)
        if hit is not None and hit[0] == (st.st_mtime_ns, st.st_size, code) and fragment_current(builder, tile, code):
            continue
        cold.append((i, j, tile))
    anchors = demand_anchors(builder)

    def order(row):
        i, j, tile = row
        return min(abs(i - a) + abs(j - b) for a, b in anchors), abs(i) + abs(j), tile

    return [tile for *_, tile in sorted(cold, key=order)]


def warm_tile(builder, tile) -> None:
    builder.built(tile)
    if retained_fragment(builder, tile) is not None:
        with builder.lock:
            builder.servable = builder.servable | {tile}


def warm_pass(builder) -> list[str]:
    """Warm every cold tile once, nearest the active demand first; the tiles warmed."""
    warmed = []
    for tile in cold_tiles(builder):
        if builder.stopped:
            break
        try:
            warm_tile(builder, tile)
            warmed.append(tile)
        except Exception as error:  # noqa: BLE001 - a request checks the tile itself
            print(f"coarse warm {tile}: {error}", file=sys.stderr, flush=True)
        time.sleep(WARM_PAUSE_S)
    return warmed


def maintain_warm(builder) -> None:
    idle_io()
    while not builder.stopped:
        try:
            warm_pass(builder)
        except Exception as error:  # noqa: BLE001 - keep warming; the next scan retries
            print(f"coarse warm: {error}", file=sys.stderr, flush=True)
        end = time.monotonic() + WARM_SCAN_S
        while not builder.stopped and time.monotonic() < end:
            time.sleep(0.5)
