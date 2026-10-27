"""Refresh derived products from declared input and code hashes.

Ground, buildings, surrounding massing, roads, props, vegetation and parked cars follow the
playable tile list (config.playable_tile_ids); a layer built for another list is stale.
Structures and bridges stay on the measured office core."""

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from turku3d import config as C
from turku3d.bridges.__main__ import code_hash as bridges_code_hash
from turku3d.ground.build import code_hash as ground_code_hash
from turku3d.layers import read_manifest
from turku3d.package.ground_textures import ground_texture_code_hash
from turku3d.roads import code_hash as roads_code_hash
from turku3d.structures.layer import code_hash as structures_code_hash
from turku3d.vegetation.__main__ import code_hash as vegetation_code_hash

_detach_lock = threading.Lock()
# Written beside a tile-following layer's MANIFEST.json after it is rebuilt for the list.
TILE_RECORD = "PLAYABLE_TILES.json"


def built_tiles(directory):
    """The tile list a layer was last built for; layers from before the list cover the core."""
    path = directory / TILE_RECORD
    return json.loads(path.read_text())["tiles"] if path.is_file() else list(C.CORE_TILES)


def tiles_current(directory):
    return built_tiles(directory) == C.playable_tile_ids()


def record_tiles(directory):
    pending = directory / (TILE_RECORD + ".tmp")
    pending.write_text(json.dumps({"tiles": C.playable_tile_ids()}) + "\n")
    pending.replace(directory / TILE_RECORD)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def code_hash(paths):
    files = sorted(p for root in paths for p in (root.rglob("*.py") if root.is_dir() else [root]))
    h = hashlib.sha256()
    for path in files:
        h.update(path.read_bytes())
    return h.hexdigest()


def declared_inputs_current(manifest):
    for name, identity in manifest.get("inputs", {}).items():
        if name in ("placement_report", "placements.json"):
            continue  # Build reports and the producer's own audit output are not inputs.
        if name == "placement_code":
            if identity != code_hash([C.REPO_ROOT / "pipeline/turku3d/placement"]):
                return False
            continue
        if isinstance(identity, dict):
            path = Path(identity["path"])
            if not path.is_file():
                return False
            stat = path.stat()
            if stat.st_size == identity.get("size") and stat.st_mtime_ns == identity.get(
                "mtime_ns"
            ):
                continue
            expected = identity.get("sha256")
        else:
            path, expected = Path(name), identity
            if not path.is_absolute():
                path = C.data_root() / path
        if not path.is_file() or sha(path) != expected:
            return False
    return True


def manifest_current(path, expected_code):
    if not path.is_file():
        return False
    manifest = json.loads(path.read_text())
    return manifest.get("code") == expected_code and declared_inputs_current(manifest)


def layer_current(path, expected_code):
    if not manifest_current(path, expected_code):
        return False
    try:
        read_manifest(path.parent)
    except (OSError, ValueError, KeyError):
        return False
    return True


def texture_sources_current(manifest_path, cache_path):
    manifest = json.loads(manifest_path.read_text())
    source_key = hashlib.sha256(json.dumps(manifest["inputs"], sort_keys=True).encode()).hexdigest()
    previous = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    signatures = {}
    for item in manifest["inputs"]:
        path = Path(item["path"])
        if not path.is_file():
            return False
        stat = path.stat()
        signature = [stat.st_size, stat.st_mtime_ns]
        signatures[str(path)] = signature
        if (
            previous.get("manifest") == source_key
            and previous.get("files", {}).get(str(path)) == signature
        ):
            continue
        if sha(path) != item["sha256"]:
            return False
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pending = cache_path.with_suffix(".tmp")
    pending.write_text(json.dumps({"manifest": source_key, "files": signatures}))
    pending.replace(cache_path)
    return True


def run(*args, extra_path=None):
    env = os.environ.copy()
    if extra_path:
        env["PYTHONPATH"] = str(extra_path) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run([sys.executable, "-m", *args], check=True, cwd=C.REPO_ROOT, env=env)


def local_directory(path, work):
    """Detach linked sandbox directories before a producer can write through them."""
    if work.resolve() == C.data_root().resolve():
        raise RuntimeError("refresh runs in sandboxes; the published data root is read-only")
    with _detach_lock:
        relative = path.relative_to(work)
        current = work
        for part in relative.parts:
            current = current / part
            if not current.is_symlink():
                continue
            source = current.resolve(strict=True)
            if not source.is_dir():
                raise ValueError(f"sandbox directory link is invalid: {current}")
            temporary = current.with_name(current.name + ".refresh-local")
            if temporary.exists() or temporary.is_symlink():
                raise FileExistsError(temporary)
            if subprocess.call(
                ["cp", "-cRL", str(source), str(temporary)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ):
                if temporary.exists():
                    shutil.rmtree(temporary)
                shutil.copytree(source, temporary)
            previous = current.with_name(current.name + ".refresh-link")
            if previous.exists() or previous.is_symlink():
                raise FileExistsError(previous)
            os.replace(current, previous)
            try:
                os.replace(temporary, current)
            except Exception:
                os.replace(previous, current)
                raise
            previous.unlink()


def ground(layers):
    path = layers / "ground/MANIFEST.json"
    if manifest_current(path, ground_code_hash()) and tiles_current(path.parent):
        return "cached"
    args = ["--tiles", *C.playable_tile_ids()]
    if path.exists():
        inputs = json.loads(path.read_text()).get("inputs", {})
        if "breaklines" in inputs:
            args += ["--breaklines", inputs["breaklines"]["path"]]
    local_directory(layers / "ground", layers.parents[1])
    run("turku3d.ground.build", *args)
    record_tiles(path.parent)
    return "rebuilt"


def buildings(layers):
    expected = buildings_code_hash()
    if manifest_current(layers / "buildings/MANIFEST.json", expected) and tiles_current(
        layers / "buildings"
    ):
        return "cached"
    local_directory(layers / "buildings", layers.parents[1])
    facade_manifest = layers.parents[1] / "textures/assembly/MANIFEST.json"
    if facade_manifest.exists():
        export = facade_manifest.parent
        local_directory(export, layers.parents[1])
        with (export / ".export.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if json.loads(facade_manifest.read_text()).get("bound"):
                snapshot = export / "APPROVED_INVENTORY.geojson"
                inventory = layers / "buildings/facades.geojson"
                old_hash = json.loads(facade_manifest.read_text()).get("inventory_sha256")
                if not snapshot.exists():
                    if not inventory.exists() or sha(inventory) != old_hash:
                        raise RuntimeError("approved facade source inventory is unavailable")
                    shutil.copy2(inventory, snapshot)
                if sha(snapshot) != old_hash:
                    raise RuntimeError("approved facade source inventory hash mismatch")
    run(
        "turku3d.buildings",
        "--jobs",
        os.environ.get("TURKU3D_JOBS", "3"),
        "--tiles",
        *C.playable_tile_ids(),
    )
    record_tiles(layers / "buildings")
    return "rebuilt"


def buildings_code_hash():
    base = C.REPO_ROOT / "pipeline/turku3d"
    sources = sorted((base / "buildings").glob("*.py")) + [
        base / "config.py",
        base / "layers.py",
        base / "package/gltf.py",
        base / "col/materials_v1.json",
    ]
    return hashlib.sha256("".join(sha(p) for p in sources).encode()).hexdigest()


def outer_buildings(layers):
    from turku3d.buildings.outer import code_hash

    directory = layers / "buildings_outer"
    if manifest_current(directory / "MANIFEST.json", code_hash()) and tiles_current(directory):
        return "cached"
    local_directory(directory, layers.parents[1])
    run("turku3d.buildings.outer")
    record_tiles(directory)
    return "rebuilt"


def stage_ground_library(textures):
    library = textures / "library"
    shared = C.data_root() / "textures/library"
    for name in ("albedo", "normal", "orm"):
        target = library / f"{name}.array.ktx2"
        source = shared / target.name
        if not target.exists() and source.is_file():
            local_directory(library, textures.parent)
            library.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            shutil.copy2(source, temporary)
            temporary.replace(target)


def ground_textures(layers, textures):
    from turku3d.package.ground_textures import publish_ground

    stage_ground_library(textures)
    tiles = C.playable_tile_ids()
    root = textures / "ground"
    code = ground_texture_code_hash()
    manifest = root / "MANIFEST.json"
    current = manifest.exists() and json.loads(manifest.read_text()).get("code_version") == code
    sources_current = manifest.exists() and texture_sources_current(
        manifest, textures.parent / "cache/refresh/ground-texture-inputs.json"
    )
    current = current and sources_current
    current = current and all(
        (textures / "library" / f"{kind}.array.ktx2").is_file()
        for kind in ("albedo", "normal", "orm")
    )
    if current:
        try:
            # Validation is read-only: publish into an empty temporary directory only if needed.
            for tile in tiles:
                spec = json.loads((root / tile / "material.json").read_text())
                for item in spec["ground_inputs"]:
                    path = layers / "ground" / Path(item["path"]).name
                    if not path.is_file() or sha(path) != item["sha256"]:
                        current = False
                        break
                for entry in spec["textures"].values():
                    path = root / tile / entry["uri"]
                    if not path.is_file() or sha(path) != entry["sha256"]:
                        current = False
                        break
        except (KeyError, FileNotFoundError, json.JSONDecodeError):
            current = False
    if current:
        return "cached"
    local_directory(root, textures.parent)
    args = ["turkutex.ground.build", "--ground-layer", str(layers / "ground"), "--out", str(root)]
    # Each tile keeps its maps while its keys match; one with current orthos redoes its splats.
    args += ["--tiles", *tiles]
    run(*args, extra_path=C.REPO_ROOT / "textures")
    # Assembly owns publication; this checks that the refreshed binding is usable.
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        bindings = publish_ground(root, Path(temp), tiles, layers / "ground")
    if set(bindings) != set(tiles):
        raise RuntimeError(f"ground refresh did not bind {sorted(set(tiles) - set(bindings))}")
    return "rebuilt"


def facades(layers, textures):
    from turku3d.package.facades import facade_bindings

    export = textures / "assembly"
    manifest = export / "MANIFEST.json"
    if not manifest.exists():
        return "absent"
    export_record = json.loads(manifest.read_text())
    bound = export_record.get("bound", [])
    current = facade_bindings(export / "facades", layers)
    if set(current) != set(bound) or export_record.get("inventory_sha256") != sha(
        layers / "buildings/facades.geojson"
    ):
        local_directory(export, textures.parent)
        report = textures.parent / "reports/facade-refresh.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        run(
            "turkutex.facade_rebind",
            "--source-inventory",
            str(export / "APPROVED_INVENTORY.geojson"),
            "--inventory",
            str(layers / "buildings/facades.geojson"),
            "--assembly",
            str(export),
            "--report",
            str(report),
            "--new-inventory",
            str(textures / "refresh-new.geojson"),
            extra_path=C.REPO_ROOT / "textures",
        )
        bound = json.loads(manifest.read_text()).get("bound", [])
        current = facade_bindings(export / "facades", layers)
        if set(current) != set(bound):
            raise RuntimeError(
                f"{len(set(bound) - set(current))} migrated facade bindings failed validation"
            )
        return "rebuilt"
    return "cached"


def props(layers):
    code = code_hash([C.REPO_ROOT / "pipeline/turku3d/props"])
    if manifest_current(layers / "props/MANIFEST.json", code) and tiles_current(layers / "props"):
        return "cached"
    local_directory(layers / "props", layers.parents[1])
    run("turku3d.props", "--area", "playable-tiles")
    record_tiles(layers / "props")
    return "rebuilt"


def roads(layers):
    if layer_current(layers / "roads/MANIFEST.json", roads_code_hash()) and tiles_current(
        layers / "roads"
    ):
        return "cached"
    local_directory(layers / "roads", layers.parents[1])
    run("turku3d.roads", "--area", "playable-tiles")
    record_tiles(layers / "roads")
    return "rebuilt"


def vegetation(layers):
    if layer_current(layers / "vegetation/MANIFEST.json", vegetation_code_hash()) and tiles_current(
        layers / "vegetation"
    ):
        return "cached"
    local_directory(layers / "vegetation", layers.parents[1])
    run("turku3d.vegetation")
    record_tiles(layers / "vegetation")
    return "rebuilt"


def parked(layers):
    """Parked cars clear the published colliders of every other layer, so they rebuild last."""
    code = code_hash([C.REPO_ROOT / "pipeline/turku3d/parked"])
    if manifest_current(layers / "traffic_static/MANIFEST.json", code) and tiles_current(
        layers / "traffic_static"
    ):
        return "cached"
    local_directory(layers / "traffic_static", layers.parents[1])
    run("turku3d.parked")
    record_tiles(layers / "traffic_static")
    return "rebuilt"


def timed(name, operation, *args):
    start = time.perf_counter()
    try:
        status = operation(*args)
    except Exception:
        print(f"{name}: failed ({time.perf_counter() - start:.3f}s)", flush=True)
        raise
    print(f"{name}: {status} ({time.perf_counter() - start:.3f}s)", flush=True)
    return status


def bridges(layers):
    if layer_current(layers / "bridges/MANIFEST.json", bridges_code_hash()):
        return "cached"
    local_directory(layers / "bridges", layers.parents[1])
    run("turku3d.bridges")
    return "rebuilt"


def structures(layers):
    path = layers / "structures/MANIFEST.json"
    if layer_current(path, structures_code_hash()):
        manifest = json.loads(path.read_text())
        if manifest["params"].get("acceptance_passed"):
            return "cached"
    local_directory(layers / "structures", layers.parents[1])
    run("turku3d.structures", "--area", "office-block", "--ground-layer", str(layers / "ground"))
    return "rebuilt"


def bridge_textures(textures):
    library = textures / "assembly/library"
    aliases = {
        "bridge_asphalt": "ground_asphalt",
        "bridge_stone": "ground_granite",
        "painted_steel": "roof_standing_seam_metal",
    }
    if all(
        (library / alias).is_dir() and (library / original).is_dir()
        for alias, original in aliases.items()
    ):
        return "cached"
    local_directory(library, textures.parent)
    changed = False
    for alias, original in aliases.items():
        source, target = library / original, library / alias
        if not source.is_dir():
            shared = C.data_root() / "textures/assembly/library" / original
            if not shared.is_dir():
                raise FileNotFoundError(f"Bridge material source missing: {shared}")
            source.parent.mkdir(parents=True, exist_ok=True)
            source.symlink_to(shared, target_is_directory=True)
            changed = True
        if target.exists():
            continue
        if target.is_symlink():
            target.unlink()
        target.symlink_to(source.name, target_is_directory=True)
        changed = True
    return "rebuilt" if changed else "cached"


def refresh(work, target=None):
    if work.resolve() == C.data_root().resolve():
        raise RuntimeError("refresh runs in sandboxes; use a sandbox work directory")
    work.mkdir(parents=True, exist_ok=True)
    layers, textures = work / "build/layers", work / "textures"
    with (work / ".refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target == "ground-textures":
            result = {
                "ground": timed("ground", ground, layers),
                "ground-textures": timed("ground-textures", ground_textures, layers, textures),
            }
            return result
        if target == "facades":
            result = {"buildings": timed("buildings", buildings, layers)}
            result["facades"] = timed("facades", facades, layers, textures)
            return result
        result = {}
        # Ground and buildings may share a linked layers ancestor. Detach it once
        # before either producer starts, but leave a fully cached tree linked.
        if layers.is_symlink() and (
            not manifest_current(layers / "ground/MANIFEST.json", ground_code_hash())
            or not manifest_current(layers / "buildings/MANIFEST.json", buildings_code_hash())
        ):
            local_directory(layers, work)
        workers = max(1, int(os.environ.get("TURKU3D_JOBS", "3")))
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(2, workers)) as pool:
            futures = {
                "ground": pool.submit(timed, "ground", ground, layers),
                "buildings": pool.submit(timed, "buildings", buildings, layers),
            }
            for name, future in futures.items():
                result[name] = future.result()
        result["structures"] = timed("structures", structures, layers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, workers)) as pool:
            futures = {
                "ground-textures": pool.submit(
                    timed, "ground-textures", ground_textures, layers, textures
                ),
                "facades": pool.submit(timed, "facades", facades, layers, textures),
                "bridges": pool.submit(timed, "bridges", bridges, layers),
                "roads": pool.submit(timed, "roads", roads, layers),
                "buildings_outer": pool.submit(timed, "buildings_outer", outer_buildings, layers),
            }
            for name in ("bridges", "buildings_outer"):
                result[name] = futures[name].result()
            # Instance placement reads every solid layer above, and each producer clears
            # the inventories published before it (placement.inputs.PLACEMENT_ORDER).
            result["props"] = timed("props", props, layers)
            result["vegetation"] = timed("vegetation", vegetation, layers)
            result["traffic_static"] = timed("traffic_static", parked, layers)
            for name in ("ground-textures", "facades", "roads"):
                result[name] = futures[name].result()
        result["bridge-textures"] = timed("bridge-textures", bridge_textures, textures)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=C.work_root())
    parser.add_argument("--target", choices=["ground-textures", "facades"])
    args = parser.parse_args(argv)
    refresh(args.work, args.target)


if __name__ == "__main__":
    main()
