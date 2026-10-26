"""Refresh derived office-block products from declared input and code hashes."""

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from turku3d import config as C
from turku3d.bridges.__main__ import code_hash as bridges_code_hash
from turku3d.ground.build import code_hash as ground_code_hash
from turku3d.layers import read_manifest
from turku3d.package.ground_textures import ground_texture_code_hash

TILES = ("t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1")


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
        if name == "placement_report":
            continue  # This is a build report, not a producer input.
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
    if manifest_current(path, ground_code_hash()):
        return "cached"
    args = []
    if path.exists():
        inputs = json.loads(path.read_text()).get("inputs", {})
        if "breaklines" in inputs:
            args = ["--breaklines", inputs["breaklines"]["path"]]
    local_directory(layers / "ground", layers.parents[1])
    run("turku3d.ground.build", *args)
    return "rebuilt"


def buildings(layers):
    base = C.REPO_ROOT / "pipeline/turku3d"
    sources = sorted((base / "buildings").glob("*.py")) + [
        base / "config.py",
        base / "layers.py",
        base / "package/gltf.py",
        base / "col/materials_v1.json",
    ]
    expected = hashlib.sha256("".join(sha(p) for p in sources).encode()).hexdigest()
    if manifest_current(layers / "buildings/MANIFEST.json", expected):
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
    run("turku3d.buildings")
    return "rebuilt"


def outer_buildings(layers):
    from turku3d.buildings.outer import code_hash

    if manifest_current(layers / "buildings_outer/MANIFEST.json", code_hash()):
        return "cached"
    local_directory(layers / "buildings_outer", layers.parents[1])
    run("turku3d.buildings.outer")
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
            for tile in TILES:
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
    if (
        manifest.exists()
        and sources_current
        and json.loads(manifest.read_text()).get("code_version") == code
    ):
        args.insert(1, "--splats-only")
    run(*args, extra_path=C.REPO_ROOT / "textures")
    # Assembly owns publication; this checks that the refreshed binding is usable.
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        bindings = publish_ground(root, Path(temp), TILES, layers / "ground")
    if set(bindings) != set(TILES):
        raise RuntimeError("ground refresh did not produce all four tile bindings")
    return "rebuilt"


def facades(layers, textures):
    from turku3d.package.assemble import facade_bindings

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
    if manifest_current(layers / "props/MANIFEST.json", code):
        return "cached"
    local_directory(layers / "props", layers.parents[1])
    run("turku3d.props", "--area", "phase1")
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


def bridge_textures(textures):
    library = textures / "assembly/library"
    local_directory(library, textures.parent)
    aliases = {
        "bridge_asphalt": "ground_asphalt",
        "bridge_stone": "ground_granite",
        "painted_steel": "roof_standing_seam_metal",
    }
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                "ground": pool.submit(timed, "ground", ground, layers),
                "buildings": pool.submit(timed, "buildings", buildings, layers),
            }
            for name, future in futures.items():
                result[name] = future.result()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                "ground-textures": pool.submit(
                    timed, "ground-textures", ground_textures, layers, textures
                ),
                "facades": pool.submit(timed, "facades", facades, layers, textures),
                "props": pool.submit(timed, "props", props, layers),
                "bridges": pool.submit(timed, "bridges", bridges, layers),
            }
            for name, future in futures.items():
                result[name] = future.result()
        result["bridge-textures"] = timed("bridge-textures", bridge_textures, textures)
        result["buildings_outer"] = timed("buildings_outer", outer_buildings, layers)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=C.work_root())
    parser.add_argument("--target", choices=["ground-textures", "facades"])
    args = parser.parse_args(argv)
    refresh(args.work, args.target)


if __name__ == "__main__":
    main()
