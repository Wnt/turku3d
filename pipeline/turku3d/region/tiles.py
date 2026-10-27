"""Grow the playable area by one L0 tile.

    python -m turku3d.region add-tile t0_2_0    # build it in this sandbox (make tile-add T=...)
    python -m turku3d.region list-tile t0_2_0   # list it in $TURKU3D_DATA for the integrator

`add-tile` appends the tile to the sandbox's playable-tiles.json, makes sure its inputs exist
(work-3x3 while that holds the tile's input area, else the tile's own `tile-<id>` area, fetched
read-through into $TURKU3D_DATA/raw), seeds the sandbox from the published layers, runs
`turku3d.refresh` (only the new tile and what depends on it rebuild) and assembles the listed
tiles with the `playable-tiles` rules. Stage times go to $TURKU3D_WORK/reports/tile-add/<tile>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from turku3d import config as C

REFRESH_LINE = re.compile(r"^([a-z_-]+): (cached|rebuilt|absent|failed) \(([\d.]+)s\)$")


def plan(tile_id: str, listed: list[str]) -> list[str]:
    """The new list, or ValueError when the tile cannot grow the area here."""
    tile = C.VisualTile.parse(tile_id)
    if tile.level != 0:
        raise ValueError(f"{tile_id}: playable tiles are L0 (t0_i_j)")
    if tile.id in listed:
        raise ValueError(f"{tile_id} is already playable")
    current = {C.VisualTile.parse(t) for t in listed}
    neighbours = {
        C.VisualTile(0, tile.i + di, tile.j + dj) for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))
    }
    if not neighbours & current:
        raise ValueError(
            f"{tile_id} does not share an edge with a playable tile; grow tile by tile"
        )
    uncovered = uncovered_lidar(tile)
    if uncovered:
        raise ValueError(
            f"{tile_id}: its input area needs lidar tiles the city does not publish "
            f"({', '.join(uncovered)}); data-manifests/laz-tiles.csv lists the coverage"
        )
    return [t.id for t in sorted(current | {tile})]


def uncovered_lidar(tile) -> list[str]:
    """LAZ tiles under the tile's input area that are missing from the city's tile list."""
    from turku3d.fetch.laz import known_tiles

    listed = {name for dataset, name in known_tiles() if dataset == "pistepilvi"}
    if not listed:
        return []  # No list to check against; the fetch reports what it cannot download.
    return [t.name for t in C.tile_input_area(tile).laz_tiles if t.name not in listed]


def input_bytes(area) -> int:
    """Raw bytes registered for an input area: its WFS, ortho and OSM files and its LAZ tiles."""
    from turku3d.fetch.common import Registry

    total = 0
    for kind in ("wfs", "ortho", "osm"):
        root = C.raw_dir(kind)
        if root.is_dir():
            records = Registry(root).records()
            total += sum(r["bytes"] for path, r in records.items() if f"/{area.name}" in f"/{path}")
    laz = Registry(C.raw_dir("laz")).records() if C.raw_dir("laz").is_dir() else {}
    names = {f"{ds}/{t.name}.laz" for ds in C.LAZ_DATASETS for t in area.laz_tiles}
    return total + sum(r["bytes"] for path, r in laz.items() if path in names)


def ensure_inputs(tile_id: str) -> dict:
    """Read-through: download only what the tile's input area lacks (fetch locks shared raw)."""
    from turku3d.fetch.cached import check

    area = C.area(C.source_area(tile_id))
    missing = check(area)
    result = {"area": area.name, "missing": len(missing), "bytes_before": input_bytes(area)}
    if missing:
        begin = time.monotonic()
        subprocess.run(
            [sys.executable, "-m", "turku3d.fetch", "all", "--area", area.name], check=True
        )
        result["fetch_seconds"] = round(time.monotonic() - begin, 3)
        missing = check(area)
        if missing:
            raise RuntimeError("inputs still missing after fetch: " + ", ".join(missing[:5]))
    result["bytes"] = input_bytes(area)
    result["fetched_bytes"] = result["bytes"] - result["bytes_before"]
    return result


def seed(work: Path, data: Path) -> list[str]:
    """Start from the published layers, textures and tiles (APFS clones) where the sandbox has none."""
    seeded = []
    for relative in ("build/layers", "textures", "dist/tiles"):
        source, target = data / relative, work / relative
        if target.exists() or target.is_symlink() or not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-cRL", str(source.resolve()), str(target)], check=True)
        seeded.append(relative)
    return seeded


def stream(command: list[str], log) -> list[str]:
    """Run a pipeline command, echoing and keeping its output lines; raise on failure."""
    lines = []
    with subprocess.Popen(
        command, cwd=C.REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as proc:
        for line in proc.stdout:
            print(line, end="", flush=True)
            log.write(line)
            lines.append(line.rstrip("\n"))
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, command)
    return lines


def tile_summary(tile_id: str, work: Path) -> dict:
    layers = work / "build/layers"
    present = sorted(
        manifest.parent.name
        for manifest in layers.glob("*/MANIFEST.json")
        if f"{tile_id}.vis.glb" in json.loads(manifest.read_text())["tiles"]
    )
    entry = json.loads((work / "dist/tiles/manifest.json").read_text())["visual"][tile_id]
    keys = ("bytes", "brotli_bytes", "ground_texture_bytes", "wire_bytes", "tris")
    return {"visual_layers": present, **{k: entry.get(k) for k in keys}}


def add_tile(tile_id: str) -> dict:
    work, data = C.work_root().resolve(), C.data_root().resolve()
    if work == data:
        raise RuntimeError("add-tile builds in a sandbox; use list-tile for the data root")
    started = time.monotonic()
    stages: dict[str, float] = {}
    listed = C.playable_tile_ids()
    published = C.playable_tile_ids(data)
    # A failed run leaves the tile in the sandbox list; rerunning resumes from cached layers.
    tiles = listed if tile_id in listed and tile_id not in published else plan(tile_id, listed)
    report_dir = work / "reports/tile-add"
    report_dir.mkdir(parents=True, exist_ok=True)

    def stage(name, operation, *args):
        begin = time.monotonic()
        result = operation(*args)
        stages[name] = round(time.monotonic() - begin, 3)
        print(f"tile-add {name}: {stages[name]:.1f}s", flush=True)
        return result

    inputs = stage("inputs", ensure_inputs, tile_id)
    seeded = stage("seed", seed, work, data)
    C.write_playable_tiles(work / C.PLAYABLE_TILES_FILE, tiles)
    with (report_dir / f"{tile_id}.log").open("w") as log:
        lines = stage("refresh", stream, [sys.executable, "-m", "turku3d.refresh"], log)
        layers = {
            m[1]: {"status": m[2], "seconds": float(m[3])}
            for m in map(REFRESH_LINE.match, lines)
            if m
        }
        command = [sys.executable, "-m", "turku3d.package.assemble", "--area", "playable-tiles"]
        out = stage("assemble", stream, [*command, "--tile", *tiles], log)
    report = {
        "tile": tile_id,
        "bbox_en": list(C.VisualTile.parse(tile_id).bbox.to_en().as_tuple()),
        "tiles": tiles,
        "inputs": inputs,
        "seeded": seeded,
        "seconds": {**stages, "total": round(time.monotonic() - started, 3)},
        "layers": layers,
        "assemble": json.loads(out[-1]),
        "result": tile_summary(tile_id, work),
    }
    (report_dir / f"{tile_id}.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def list_tile(tile_id: str) -> dict:
    path = C.playable_tiles_path(C.data_root())
    return C.write_playable_tiles(path, plan(tile_id, C.read_playable_tiles(path)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.region", description=__doc__)
    ap.add_argument("action", choices=["add-tile", "list-tile"])
    ap.add_argument("tile")
    a = ap.parse_args(argv)
    result = add_tile(a.tile) if a.action == "add-tile" else list_tile(a.tile)
    print(json.dumps(result if a.action == "list-tile" else result["seconds"]))
    return 0
