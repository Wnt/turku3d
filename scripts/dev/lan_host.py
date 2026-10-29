"""Read-only live-cache evidence, executed on OCI through its active run.sh."""

import collections
import hashlib
import json
import shutil
import statistics
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen


def get(path):
    with urlopen("http://127.0.0.1:7764/" + path, timeout=5) as response:
        return json.load(response)


def snapshot():
    from turku3d import config as C
    from turku3d.coarse.idle import route_walk, spawn_tiles
    from turku3d.coarse.service import Builder
    from turku3d.coarse.sources import NoCoverage, Sources

    root = Path("/srv/turku3d/work/coarse")
    home = Path("/home/ubuntu/turku3d-drafts")
    scheduler = json.loads((root / ".scheduler.json").read_text())
    code = scheduler["code"]
    evidence = root.parent / "lan-party"
    evidence.mkdir(exist_ok=True)
    navigation = get("coarse/navigation.json")
    nav_hash = hashlib.sha256(json.dumps(navigation, sort_keys=True).encode()).hexdigest()
    plan_path = evidence / f"plan-{code}-{nav_hash[:12]}.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
    else:
        settings = json.loads((home / "repo/tools/remote-drafts/config.json").read_text())
        walk, summary = route_walk(navigation, settings["tile_bounds"])
        plan = dict(walk=walk, summary=summary, spawn=spawn_tiles())
        plan_path.write_text(json.dumps(plan) + "\n")
    sources = Sources()
    checker = SimpleNamespace(
        manifest=lambda tile: root / tile / "manifest.json",
        input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
        structural_input_key=lambda tile: sources.input_key(
            C.VisualTile.parse(tile), first_pass=True
        ),
    )
    covered, missing, broken = set(), [], []
    unavailable = set(scheduler.get("prebuild", {}).get("unavailable", []))
    unavailable.update(
        tile for tile, error in scheduler.get("failed", {}).items() if "NoCoverage:" in error
    )
    payload_bytes = []
    for _, tile in plan["walk"]:
        if tile in unavailable:
            continue
        path = checker.manifest(tile)
        try:
            sources.lidar_paths(C.VisualTile.parse(tile))
            manifest = json.loads(path.read_text())
            if manifest.get("pass_kind") != "full" or not Builder.built(checker, tile, code):
                missing.append(tile)
                continue
            entries = [e for key in ("visual", "collision") for e in manifest.get(key, {}).values()]
            if len(manifest.get("collision", {})) != 16 or not manifest.get("visual"):
                broken.append(tile)
                continue
            if any(
                not (path.parent / e["url"]).is_file()
                or (path.parent / e["url"]).stat().st_size != e["bytes"]
                for e in entries
            ):
                broken.append(tile)
                continue
            covered.add(tile)
            payload_bytes.append(sum(e["bytes"] for e in entries))
        except NoCoverage:
            unavailable.add(tile)
        except (OSError, ValueError, KeyError):
            missing.append(tile)
    rows = []
    log = home / "reports/service.log"
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 4_000_000))
        for line in stream.read().decode(errors="replace").splitlines():
            try:
                row = json.loads(line)
                if "outcome" in row and row.get("at", 0) > time.time() - 1800:
                    rows.append(row)
            except ValueError:
                pass
    timings = {}
    for kind in ("first", "full"):
        values = [r["seconds"] for r in rows if r.get("pass") == kind and r["outcome"] == "built"]
        timings[kind] = dict(
            count=len(values),
            mean=statistics.mean(values) if values else None,
            p50=statistics.median(values) if values else None,
        )
    seconds_per_tile = sum(timings[k]["mean"] or 0 for k in timings)

    def coverage(tiles):
        tiles = set(tiles)
        count = len(tiles & covered)
        absent = len(tiles & unavailable)
        return dict(
            cached=count,
            total=len(tiles),
            percent=round(100 * count / len(tiles), 2),
            unavailable=absent,
            ideal_eta_seconds=round((len(tiles) - count - absent) * seconds_per_tile / 4)
            if all(timings[k]["count"] for k in timings)
            else None,
        )

    route_tiles = [tile for _, tile in plan["walk"][: plan["summary"]["route_tiles"]]]
    memory = {
        line.split(":")[0]: int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
    }
    memory["SwapUsed"] = memory["SwapTotal"] - memory["SwapFree"]
    return dict(
        at=time.time(),
        commit=(home / "SOURCE_COMMIT").read_text().strip(),
        code=code,
        health=get("coarse/health"),
        multiplayer=get("healthz"),
        coverage=dict(
            spawn=coverage(plan["spawn"]),
            routes=coverage(route_tiles),
            wider=coverage(tile for _, tile in plan["walk"]),
        ),
        plan=str(plan_path),
        plan_summary=plan["summary"],
        missing=missing,
        broken=broken,
        unavailable=sorted(unavailable),
        disk_free={p: shutil.disk_usage(p).free for p in ("/", "/srv/turku3d")},
        memory={
            k: memory[k] for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "SwapUsed")
        },
        cpu=Path("/proc/stat").read_text().splitlines()[0],
        load=Path("/proc/loadavg").read_text().strip(),
        timings=timings,
        outcomes=dict(collections.Counter(r["outcome"] for r in rows)),
        payload_bytes_mean=statistics.mean(payload_bytes) if payload_bytes else None,
    )


if __name__ == "__main__":
    print(json.dumps(snapshot()))
