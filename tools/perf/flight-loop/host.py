"""Flight-loop OCI observer and narrowly scoped, reversible built-output reset.

Run over SSH with Python stdin; never modifies sources, shared stages or raw data.
"""

import fcntl
import json
import os
import re
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.request import urlopen

ROOT = Path("/srv/turku3d/work/coarse")
HOME = Path("/home/ubuntu/turku3d-drafts")
TILES = ["t0_-1_2", "t0_0_2", "t0_1_2", "t0_0_3"]


def emit(kind, **data):
    print(json.dumps(dict(at=time.time(), kind=kind, **data)), flush=True)


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def get(port, route):
    start = time.monotonic()
    try:
        with urlopen(f"http://127.0.0.1:{port}/{route}", timeout=2) as response:
            return dict(ms=1000 * (time.monotonic() - start), body=json.load(response))
    except Exception as error:
        return dict(ms=1000 * (time.monotonic() - start), error=str(error))


def cold(label):
    # Retiring producers are not listed in the active producer's scheduler file.
    # Their full passes still own the same tile lock and may still publish outputs.
    with ExitStack() as stack:
        for tile in sorted(TILES):
            directory = ROOT / tile
            directory.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context((directory / ".retention.lock").open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    f"route tile still building, including retiring producers: {tile}"
                ) from error
        _cold(label)


def _cold(label):
    if not re.fullmatch(r"[a-z0-9-]+", label):
        raise ValueError("invalid iteration label")
    scheduler = read(ROOT / ".scheduler.json") or {}
    busy = set(scheduler.get("building", [])) | set(scheduler.get("upgrading", []))
    if busy.intersection(TILES):
        raise RuntimeError(f"route tiles building: {sorted(busy.intersection(TILES))}")
    backup = ROOT.parent / "flight-loop" / label
    backup.mkdir(parents=True, exist_ok=False)
    moved = []
    for tile in TILES:
        source = ROOT / tile
        target = backup / tile
        target.mkdir()
        # Keep facade programs/candidates and every shared or per-stage cache in place.
        for name in (
            "manifest.json",
            "collision.json",
            "first.json",
            "last-full.json",
            "vis.glb",
            "roads.json",
            "stage-times.json",
            "col",
        ):
            file = source / name
            if file.exists():
                file.rename(target / name)
                moved.append(f"{tile}/{name}")
        for pattern in ("vis.*.glb", "roads.*.json", "bundle.*.col"):
            for file in source.glob(pattern):
                file.rename(target / file.name)
                moved.append(f"{tile}/{file.name}")
    emit("cold", backup=str(backup), tiles=TILES, moved=moved)


def processes():
    rows = []
    for path in Path("/proc").glob("[0-9]*"):
        try:
            command = (path / "comm").read_text().strip()
            if command not in ("python", "python3", "node", "caddy", "forwarder-agent"):
                continue
            status = (path / "status").read_text()
            stat = (path / "stat").read_text().split(") ")[1].split()
            values = {
                line.split(":")[0]: line.split(":")[1].strip() for line in status.splitlines()
            }
            rows.append(
                dict(
                    pid=int(path.name),
                    name=command,
                    cpu_ticks=int(stat[11]) + int(stat[12]),
                    rss=values.get("VmRSS"),
                    swap=values.get("VmSwap"),
                    io=(path / "io").read_text(),
                )
            )
        except (OSError, ValueError):
            pass
    return rows


def monitor(seconds):
    until = time.monotonic() + seconds
    log = HOME / "reports/service.log"
    offset = log.stat().st_size
    stages = {}
    emit(
        "start",
        commit=(HOME / "SOURCE_COMMIT").read_text().strip(),
        clock_ticks=os.sysconf("SC_CLK_TCK"),
        freeze=read(HOME / "LAN_FREEZE.json"),
    )
    while time.monotonic() < until:
        start = time.monotonic()
        scheduler = read(ROOT / ".scheduler.json")
        active = (HOME / "ACTIVE_PORT").read_text().strip()
        emit(
            "sample",
            health=get(7764, "coarse/health"),
            producer=get(active, "coarse/health"),
            multiplayer=get(7700, "healthz"),
            scheduler=scheduler,
            processes=processes(),
            memory=Path("/proc/meminfo").read_text(),
            cpu=Path("/proc/stat").read_text().splitlines()[0],
            pressure={p.name: p.read_text() for p in Path("/proc/pressure").iterdir()},
            zram={p.name: p.read_text() for p in Path("/sys/block/zram0").glob("*_stat")},
        )
        with log.open() as stream:
            stream.seek(offset)
            for line in stream:
                emit("service", line=line.rstrip())
            offset = stream.tell()
        for tile in TILES:
            file = ROOT / tile / "stage-times.json"
            if file.exists() and stages.get(tile) != file.stat().st_mtime_ns:
                stages[tile] = file.stat().st_mtime_ns
                emit("stages", tile=tile, mtime=file.stat().st_mtime, report=read(file))
        time.sleep(max(0, 3 - (time.monotonic() - start)))


if __name__ == "__main__":
    if len(sys.argv) > 3:
        TILES = sys.argv[3].split(",")
        if not all(re.fullmatch(r"t0_-?\d+_-?\d+", tile) for tile in TILES):
            raise ValueError("invalid tile list")
    if sys.argv[1] == "cold":
        cold(sys.argv[2])
    elif sys.argv[1] == "monitor":
        monitor(float(sys.argv[2]))
