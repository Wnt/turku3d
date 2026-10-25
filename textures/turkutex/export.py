"""Export QA-approved, geometry-matched assets into an assembler lookup tree."""

import argparse
import fcntl
import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .facade_batch import asset_path, safe_id, write_json
from .library_batch import material_valid
from .post.pipeline import sha
from .streetview.facades import load_facades


def matches(a, b):
    return (
        a.props.get("building_id") == b.props.get("building_id")
        and np.allclose(a.p0, b.p0, rtol=0, atol=0.001)
        and np.allclose(a.p1, b.p1, rtol=0, atol=0.001)
        and abs(a.height_m - b.height_m) <= 0.001
        and a.base_h is not None
        and b.base_h is not None
        and abs(a.base_h - b.base_h) <= 0.001
        and a.normal_side == b.normal_side
    )


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or sha(source) != sha(target):
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copy2(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def export_lock(output: Path):
    """Serialize all writers, including manifest reads and facade publication."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".export.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facades", type=Path, help="authoritative buildings facade list")
    ap.add_argument("--source-inventory", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True, help="staged textures root")
    ap.add_argument("--qa", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True, help="separate assembler textures root")
    ap.add_argument(
        "--merge", action="store_true", help="retain prior bindings; require identical inventory"
    )
    a = ap.parse_args(argv)
    if a.source.resolve() == a.output.resolve():
        raise ValueError("export must not overwrite production staging")
    with export_lock(a.output):
        result = export_assets(a)
    print(json.dumps(result))
    return 0


def export_assets(a):
    """Caller holds export_lock for the entire publication transaction."""
    inventory_hash = sha(a.facades)
    previous = {}
    manifest = a.output / "MANIFEST.json"
    if a.merge and manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous.get("inventory_sha256") != inventory_hash:
            raise ValueError("shared export inventory changed; rebuild into a separate root")
    qa = json.loads(a.qa.read_text())
    if qa["inventory_sha256"] != sha(a.source_inventory):
        raise ValueError("QA inventory hash mismatch")
    accepted = {r["facade_id"] for r in qa["rows"] if r["complete"] and r["street_facing"]}
    source = [f for f in load_facades(a.source_inventory) if f.id in accepted]
    kept, bound = set(), []
    for f in load_facades(a.facades):
        candidates = [s for s in source if matches(s, f)]
        if len(candidates) != 1:
            continue
        old = asset_path(candidates[0], a.source).with_suffix(".json")
        meta = json.loads(old.read_text())
        target = asset_path(f, a.output)
        files = {}
        for kind, name in meta["files"].items():
            suffix = ".ktx2" if kind == "albedo" else f".{kind}.ktx2"
            dest = target.parent / (target.name + suffix)
            if sha(old.parent / name) != meta["hashes"][name]:
                raise ValueError("source asset changed after QA")
            copy(old.parent / name, dest)
            kept.add(dest)
            files[kind] = dest.name
        meta.update(
            building_id=f.props["building_id"], facade_index=f.props["facade_index"], files=files
        )
        meta["hashes"] = {name: sha(target.parent / name) for name in files.values()}
        dest = target.with_suffix(".json")
        write_json(dest, meta)
        kept.add(dest)
        bound.append(f.id)
    if not a.merge:
        for old in (a.output / "facades").glob("*/*"):
            if old.is_file() and old not in kept:
                old.unlink()
    library = []
    for directory in sorted((a.source / "library").glob("*")):
        if directory.is_dir() and material_valid(directory):
            key = safe_id(directory.name)
            for name in ("albedo.ktx2", "normal.ktx2", "orm.ktx2", "material.json"):
                copy(directory / name, a.output / "library" / key / name)
            library.append(key)
    result = {
        "inventory_sha256": inventory_hash,
        "bound": sorted(set(previous.get("bound", [])) | set(bound)),
        "library": sorted(set(previous.get("library", [])) | set(library)),
    }
    write_json(a.output / "MANIFEST.json", result)
    return result
