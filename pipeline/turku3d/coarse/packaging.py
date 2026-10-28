"""Content-addressed draft visual packaging, independent of generator source versions."""

import json
import os
from pathlib import Path

from turku3d import config as C
from turku3d.facade_assets import stored_request
from turku3d.facade_kit import ensure_kit, parameter_hash
from turku3d.package.facades import glb_buildings
from turku3d.stage_cache import StageCache, source_key


def package_visual(tile, layer_files, stage, work, library, *, first_pass=False):
    """Reuse a packed visual when emitted geometry, material files and packaging code match."""
    from turku3d.tile_layer import identities

    from .build import BRIDGE, _package_visual, _write

    files = {f"input/{i}": p for i, p in enumerate(layer_files)}
    for directory, _, names in os.walk(library, followlinks=True):
        for name in sorted(names):
            path = Path(directory) / name
            files["library/" + str(path.relative_to(library))] = path
    building_keys = set().union(*(glb_buildings(p.read_bytes()) for p in layer_files))
    stored = stored_request(building_keys)
    for identity in stored["facadeBindings"]:
        building, side = identity.rsplit("_", 1)
        directory = Path(stored["facades"]) / building
        sidecar = directory / f"{side}.json"
        files["facades/" + identity] = sidecar
        for name in json.loads(sidecar.read_text())["files"].values():
            files[f"facades/{building}/{name}"] = directory / name
    kit = None if first_pass else ensure_kit(work / "shared")
    if kit:
        for slot, path in kit["maps"].items():
            files["kit/" + slot] = Path(path)
        kit = {k: v for k, v in kit.items() if k != "maps"}
    cache = StageCache(work / ".stages" / tile.id)

    def package():
        visual, stats = _package_visual(
            tile,
            layer_files,
            stage,
            work,
            library,
            first_pass=first_pass,
            building_keys=building_keys,
        )
        assets = {
            name: (stage / "shared" / name).read_bytes() for name in stats.get("shared_images", [])
        }
        return visual, stats, assets

    visual, stats, assets = cache.run(
        "package",
        source_key(
            *sorted(BRIDGE.parent.glob("*.mjs")),
            Path(__file__).parents[1] / "stage_cache.py",
            C.REPO_ROOT / "pipeline/env/package-lock.json",
            _package_visual,
        ),
        [tile.id, first_pass, identities(files), kit, parameter_hash(building_keys)],
        package,
    )
    for name, data in assets.items():
        _write(work / "shared" / name, data)
    cache.report(stage / "package-times.json")
    return visual, stats
