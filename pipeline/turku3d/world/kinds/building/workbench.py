"""One-building workbench: a stored description becomes a GLB the preview page shows beside the photo.

  python -m turku3d.world.kinds.building.workbench build DESCRIPTION.json [--view VIEW_ID] [--name NAME]
  python -m turku3d.world.kinds.building.workbench watch DESCRIPTION.json [--view VIEW_ID]

`build` reads the building's LoD2 shell (cached per key under $TURKU3D_WORK/workbench/shells), compiles the
effective description and writes $TURKU3D_WORK/workbench/<name>/building.glb with meta.json (camera of the view,
timings, the build report). The page game/building-workbench.html reloads the GLB when meta.json changes.
`watch` rebuilds whenever the description file or the generator's code changes.
"""

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

from . import compile as compile_module
from . import describe, description, glb

REPO = Path(__file__).resolve().parents[5]


def work_root() -> Path:
    return Path(os.environ.get("TURKU3D_WORK", Path.home() / "turku3d/data")) / "workbench"


def view_pose(view_id: str) -> dict | None:
    for line in (REPO / "qa/views/views.jsonl").read_text().splitlines():
        if line.strip():
            view = json.loads(line)
            if view["id"] == view_id:
                return view
    return None


def shell_for(key: str, near) -> tuple[dict, dict]:
    """(shell, register) of a building, from the workbench cache or the LoD2 source."""
    cache = work_root() / "shells" / f"{key}.json"
    if cache.exists():
        stored = json.loads(cache.read_text())
        return stored["shell"], stored["register"]
    # The shell the build itself discovers: the shared source readers fetch what the raw tree does not hold, so
    # the workbench works wherever the build does.
    from ...api import Area
    from ...sources import Sources
    from .. import building_shell

    stubs = building_shell.discover(Area(float(near[0]), float(near[1]), 10.0), Sources(log=lambda *_: None))
    stub = next((s for s in stubs if s.id == f"building/{key}"), None)
    if stub is None:
        raise LookupError(f"no building {key} within 10 m of {near[0]:.0f}, {near[1]:.0f}")
    shell, register = stub.facts["shell"], stub.facts["register"]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"shell": shell, "register": register}))
    return shell, register


def build(path: Path, view_id: str | None, name: str | None) -> dict:
    started = time.perf_counter()
    stored = json.loads(path.read_text())
    problems = description.errors(stored)
    key = stored["id"].split("/", 1)[1]
    name = name or path.stem
    view = view_pose(view_id) if view_id else None
    near = None
    for wall in stored.get("walls", []):
        if "frame" in wall:
            near = wall["frame"]["p0"]
            break
    if near is None and view:
        near = (view["pose"]["E"], view["pose"]["N"])
    if near is None:
        raise SystemExit(
            "the description has no wall frame: give --view so the building can be found"
        )
    shell, register = shell_for(key, near)
    read_at = time.perf_counter()
    effective = describe.effective(key, shell, register, stored)
    output = compile_module.compile(effective)
    compiled_at = time.perf_counter()
    target = work_root() / name
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / "building.glb.tmp"
    stats = glb.write(output, temporary, effective["id"])
    temporary.replace(target / "building.glb")
    done = time.perf_counter()
    meta = {
        "name": name,
        "id": effective["id"],
        "revision": time.time_ns(),
        "origin": list(output.origin),
        "view": view,
        "register": register,
        "style": effective.get("style"),
        "report": problems + output.report,
        "stats": stats,
        "timings_ms": {
            "read": round((read_at - started) * 1000),
            "compile": round((compiled_at - read_at) * 1000),
            "write": round((done - compiled_at) * 1000),
            "total": round((done - started) * 1000),
        },
    }
    (target / "meta.json.tmp").write_text(json.dumps(meta, indent=1))
    (target / "meta.json.tmp").replace(target / "meta.json")
    return meta


def _report(meta: dict):
    t = meta["timings_ms"]
    print(
        f"{meta['name']}: {meta['stats']['triangles']} triangles, {meta['stats']['materials']} materials, "
        f"{meta['stats']['bytes'] / 1e6:.2f} MB in {t['total']} ms (read {t['read']}, compile {t['compile']}, write {t['write']})"
    )
    for line in meta["report"]:
        print(f"  ! {line}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="building.workbench")
    parser.add_argument("command", choices=["build", "watch"])
    parser.add_argument("description", type=Path)
    parser.add_argument("--view")
    parser.add_argument("--name")
    args = parser.parse_args(argv)
    if args.command == "build":
        _report(build(args.description, args.view, args.name))
        return 0
    package = Path(__file__).parent
    seen = None
    while True:
        stamp = (
            args.description.stat().st_mtime_ns,
            *(p.stat().st_mtime_ns for p in sorted(package.glob("*.py"))),
        )
        if stamp != seen:
            seen = stamp
            try:
                for module in [
                    m
                    for name, m in sys.modules.items()
                    if name.startswith(__package__ + ".") and not name.endswith(".workbench")
                ]:
                    importlib.reload(module)
                _report(build(args.description, args.view, args.name))
            except Exception as error:  # the watcher survives a broken edit and says what broke
                print(f"build failed: {type(error).__name__}: {error}")
        time.sleep(0.25)


if __name__ == "__main__":
    raise SystemExit(main())
