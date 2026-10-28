"""Grow the playable area by one L0 tile.

    python -m turku3d.region add-tile t0_2_0    # build it in this sandbox (make tile-add T=...)
    python -m turku3d.region list-tile t0_2_0   # list it in $TURKU3D_DATA for the integrator

`add-tile` appends the tile to the sandbox's playable-tiles.json, makes sure its inputs exist
(work-3x3 while that holds the tile's input area, else the tile's own `tile-<id>` area, fetched
read-through into $TURKU3D_DATA/raw, plus LOD2 buildings for the surrounding massing ring), seeds the sandbox from the published layers, runs
`turku3d.refresh` (only the new tile and what depends on it rebuild), gives the tile's street
walls without a map rough fast-pass maps (`turkutex.facade_fast`, in the sandbox's facade
assembly) and assembles the listed tiles with the `playable-tiles` rules. It ends by putting the
new tile and its listed edge neighbours, where their bytes differ from the live release, into the
tile store as per-tile entries (scripts/dev/tile_store.py put-tiles), so the localhost game streams
the tile as soon as it is listed. Stage times go to $TURKU3D_WORK/reports/tile-add/<tile>.json.
`list-tile` also publishes those fast maps into the data root's facade approvals, where the
integrator's data lane picks them up.
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
        C.VisualTile(0, tile.i + di, tile.j + dj)
        for di in (-1, 0, 1)
        for dj in (-1, 0, 1)
        if di or dj
    }
    if not neighbours & current:
        raise ValueError(f"{tile_id} does not touch a playable tile; grow tile by tile")
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


def ring_inputs(tiles) -> list[str]:
    """The LOD2 buildings of the massing ring around the new list (buildings.outer)."""
    from turku3d.buildings.outer import fetch_missing

    return fetch_missing(tiles)


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


def edge_neighbours(tile_id: str, listed: list[str]) -> list[str]:
    """Listed tiles sharing an edge with tile_id: their seam rows and border collision tiles change with it."""
    tile = C.VisualTile.parse(tile_id)
    near = {
        C.VisualTile(0, tile.i + di, tile.j + dj).id
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))
    }
    return sorted(t for t in listed if t in near)


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
    # A failed run or a later repair leaves the tile in the sandbox list; rerunning
    # resumes from cached layers even after the data root has listed the tile.
    tiles = listed if tile_id in listed else plan(tile_id, listed)
    report_dir = work / "reports/tile-add"
    report_dir.mkdir(parents=True, exist_ok=True)

    def stage(name, operation, *args):
        begin = time.monotonic()
        result = operation(*args)
        stages[name] = round(time.monotonic() - begin, 3)
        print(f"tile-add {name}: {stages[name]:.1f}s", flush=True)
        return result

    inputs = stage("inputs", ensure_inputs, tile_id)
    inputs["massing_ring_fetched"] = stage("ring", ring_inputs, tiles)
    seeded = stage("seed", seed, work, data)
    C.write_playable_tiles(work / C.PLAYABLE_TILES_FILE, tiles)
    with (report_dir / f"{tile_id}.log").open("w") as log:
        lines = stage("refresh", stream, [sys.executable, "-m", "turku3d.refresh"], log)
        layers = {
            m[1]: {"status": m[2], "seconds": float(m[3])}
            for m in map(REFRESH_LINE.match, lines)
            if m
        }
        fast = stage("facades-fast", fast_facades, tile_id, work, log)
        command = [sys.executable, "-m", "turku3d.package.assemble", "--area", "playable-tiles"]
        out = stage("assemble", stream, [*command, "--tile", *tiles], log)
        offered = [tile_id, *edge_neighbours(tile_id, tiles)]
        store = [sys.executable, str(C.REPO_ROOT / "scripts/dev/tile_store.py"), "put-tiles"]
        store += ["--repo", str(C.REPO_ROOT), "--tiles-dir", str(work / "dist/tiles")]
        stage("put-tile", stream, [*store, "--published", str(data), "--tiles", *offered], log)
    report = {
        "tile": tile_id,
        "bbox_en": list(C.VisualTile.parse(tile_id).bbox.to_en().as_tuple()),
        "tiles": tiles,
        "inputs": inputs,
        "seeded": seeded,
        "seconds": {**stages, "total": round(time.monotonic() - started, 3)},
        "layers": layers,
        "facades_fast": fast,
        "assemble": json.loads(out[-1]),
        "result": tile_summary(tile_id, work),
    }
    (report_dir / f"{tile_id}.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def fast_run(tile_id: str, work: Path) -> tuple[Path, list[str]]:
    """The fast pass's run directory for a tile and the arguments every step shares."""
    run = work / "textures/fast" / tile_id
    return run, [
        "--run",
        str(run),
        "--inventory",
        str(work / "build/layers/buildings/facades.geojson"),
    ]


def facade_fast(step: str, args: list[str], log) -> dict:
    python = C.REPO_ROOT / "textures/.venv/bin/python"
    return json.loads(stream([str(python), "-m", "turkutex.facade_fast", step, *args], log)[-1])


def fast_facades(tile_id: str, work: Path, log) -> dict:
    """Rough maps for the tile's street walls without a generated or derived map: references,
    one cached vision call per wall, procedural maps bound in the sandbox's facade assembly."""
    assembly = work / "textures/assembly"
    run, common = fast_run(tile_id, work)
    refs = ["--refs", str(work / "textures/fast-refs")]
    staging = ["--staging", str(run / "staging")]
    wfs = C.raw_dir("wfs") / C.source_area(tile_id)
    result = {
        "plan": facade_fast("plan", [*common, "--assembly", str(assembly), "--tiles", tile_id], log)
    }
    result["refs"] = {
        k: v for k, v in facade_fast("refs", [*common, *refs], log).items() if k != "errors"
    }
    result["describe"] = facade_fast("describe", [*common, *refs], log)
    result["render"] = facade_fast("render", [*common, *staging, "--wfs", str(wfs)], log)
    result["publish"] = facade_fast(
        "publish", [*common, *staging, "--assembly", str(assembly)], log
    )
    return result


def list_tile(tile_id: str) -> dict:
    path = C.playable_tiles_path(C.data_root())
    current = C.read_playable_tiles(path)
    listed = C.write_playable_tiles(path, current if tile_id in current else plan(tile_id, current))
    run, common = fast_run(tile_id, C.work_root())
    if (run / "staging/fast.json").exists():
        approvals = C.data_root() / "facade-approvals/assembly"
        reports = C.work_root() / "reports/tile-add"
        reports.mkdir(parents=True, exist_ok=True)
        with (reports / f"{tile_id}.fast-publish.log").open("w") as log:
            published = facade_fast(
                "publish",
                [*common, "--staging", str(run / "staging"), "--assembly", str(approvals)],
                log,
            )
        print(json.dumps({"facades_fast": published}), file=sys.stderr)
    return listed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.region", description=__doc__)
    ap.add_argument("action", choices=["add-tile", "list-tile"])
    ap.add_argument("tile")
    a = ap.parse_args(argv)
    result = add_tile(a.tile) if a.action == "add-tile" else list_tile(a.tile)
    print(json.dumps(result if a.action == "list-tile" else result["seconds"]))
    return 0
