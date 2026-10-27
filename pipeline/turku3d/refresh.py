"""Refresh derived products from declared input and code hashes.

Ground, buildings, surrounding massing, roads, props, landmarks, vegetation and parked cars
follow the playable tile list (config.playable_tile_ids). Per-tile layers (turku3d.tile_layer:
roads, structures, bridges, props, landmarks, vegetation, parked cars and the massing ring)
keep a key per tile and rebuild only stale tiles; ground and buildings keep their own
per-tile keys and record the list they were built for."""

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
from turku3d import tile_layer
from turku3d.ground.build import code_hash as ground_code_hash
from turku3d.package.ground_textures import ground_texture_code_hash

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


def declared_inputs_current(manifest):
    for name, identity in manifest.get("inputs", {}).items():
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
    from turku3d.buildings.outer import PRODUCER, ring

    return per_tile(layers, PRODUCER, "turku3d.buildings.outer", ring(C.playable_tile_ids()))


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


def per_tile(layers, producer, module, tiles=None):
    """A per-tile layer (turku3d.tile_layer) rebuilds only its stale tiles, in its producer."""
    directory = layers / producer.layer
    tiles = C.playable_tile_ids() if tiles is None else tiles
    if (directory / "MANIFEST.json").is_file() and not tile_layer.stale(directory, producer, tiles):
        return "cached"
    local_directory(directory, layers.parents[1])
    run(module)
    return "rebuilt"


def props(layers):
    from turku3d.props import PRODUCER

    return per_tile(layers, PRODUCER, "turku3d.props")


def roads(layers):
    from turku3d.roads.layer import PRODUCER

    return per_tile(layers, PRODUCER, "turku3d.roads")


def landmarks(layers):
    from turku3d.landmarks import PRODUCER

    return per_tile(layers, PRODUCER, "turku3d.landmarks")


def vegetation(layers):
    from turku3d.vegetation.layer import PRODUCER

    return per_tile(layers, PRODUCER, "turku3d.vegetation")


def parked(layers):
    """Parked cars clear the published colliders of every other layer, so they rebuild last."""
    from turku3d.parked.layer import PRODUCER

    return per_tile(layers, PRODUCER, "turku3d.parked")


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
    from turku3d.bridges.layer import PRODUCER

    return per_tile(layers, PRODUCER, "turku3d.bridges")


def structures(layers):
    from turku3d.structures.producer import PRODUCER

    return per_tile(layers, PRODUCER, "turku3d.structures")


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
                "landmarks": pool.submit(timed, "landmarks", landmarks, layers),
                "buildings_outer": pool.submit(timed, "buildings_outer", outer_buildings, layers),
            }
            # Parked cars clear every solid layer, landmarks included.
            for name in ("bridges", "buildings_outer", "landmarks"):
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
