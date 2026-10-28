"""Existing generated facade maps and descriptions shared by local and remote drafts.

The public store contains runtime metadata and generated KTX2 maps only. It never
contains reference images, job records or private source paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from functools import lru_cache
from pathlib import Path

from turku3d import config as C

META_FIELDS = (
    "facade_id",
    "building_id",
    "facade_index",
    "width_m",
    "height_m",
    "uv",
    "files",
    "hashes",
    "qa",
    "source_mode",
    "fast_description",
    "derived_from",
)


def opaque(key):
    return hashlib.sha256(str(key).encode()).hexdigest() + ".json"


def root():
    """An explicit store, the sandbox export, then the shared input store."""
    explicit = os.environ.get("TURKU3D_FACADE_ASSETS")
    if explicit:
        return Path(explicit)
    own = C.work_root() / "facade-assets"
    return own if (own / "index.json").exists() else C.data_root() / "facade-assets"


def assembly():
    return C.data_root() / "textures/assembly"


def permitted(meta):
    if meta.get("method"):
        return False
    mode = meta.get("source_mode")
    return (
        mode == "generate"
        and bool(meta.get("generated_qa"))
        or mode == "fast"
        and bool(meta.get("fast_description"))
        and not meta.get("references")
        or mode in ("derived", "derived-neighbour")
        and bool(meta.get("derived_from"))
    )


@lru_cache(maxsize=2)
def _legacy(directory, stamp):
    directory = Path(directory)
    inventory = directory / "APPROVED_INVENTORY.geojson"
    if not inventory.is_file():
        return {}
    lines = {}
    for f in json.loads(inventory.read_text())["features"]:
        p = f["properties"]
        lines[f"{p['building_id']}_{p['facade_index']}"] = f
    result = {}
    for fid in json.loads((directory / "MANIFEST.json").read_text()).get("bound", []):
        building, index = fid.rsplit("_", 1)
        path = directory / "facades" / building / f"{index}.json"
        if not path.exists() or fid not in lines:
            continue
        meta = json.loads(path.read_text())
        if not permitted(meta):
            continue
        feature = lines[fid]
        row = result.setdefault(building, dict(building_key=building, facades=[]))
        row["facades"].append(
            dict(
                metadata={k: meta[k] for k in META_FIELDS if k in meta},
                coordinates=feature["geometry"]["coordinates"],
                base_height=feature["properties"]["base_height"],
                normal=feature["properties"].get("normal"),
            )
        )
    return result


def legacy_rows(directory=None):
    directory = Path(directory or assembly())
    manifest = directory / "MANIFEST.json"
    return _legacy(str(directory), manifest.stat().st_mtime_ns) if manifest.exists() else {}


def building(key):
    path = root() / "buildings" / opaque(key)
    if path.is_file():
        return json.loads(path.read_text())
    return legacy_rows().get(str(key), {})


def description_row(key):
    """Sandbox descriptions override shared results; all lookups use stable building ids."""
    for directory in (
        C.work_root() / "facade-params",
        C.data_root() / "facade-params",
        root() / "facade-params",
    ):
        path = directory / opaque(key)
        if path.is_file():
            return json.loads(path.read_text())
    candidates = [
        f["metadata"]
        for f in building(key).get("facades", [])
        if f["metadata"].get("fast_description", {}).get("description")
    ]
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda m: (
            m["fast_description"].get("source") in ("reference", "astra"),
            m["width_m"],
        ),
    )
    return dict(
        building_key=str(key),
        description=best["fast_description"]["description"],
        width_m=best["width_m"],
        engine="stored",
        source="existing-generated-facade",
    )


def stored_request(keys):
    """Material binder inputs for only the buildings present in this draft tile."""
    rows = [building(key) for key in sorted(set(map(str, keys)))]
    directory = root() if (root() / "index.json").exists() else assembly()
    return dict(
        facades=str(directory / "facades"),
        facadeBindings=[f["metadata"]["facade_id"] for b in rows for f in b.get("facades", [])],
    )


def building_hash(key):
    return hashlib.sha256(json.dumps(building(key), sort_keys=True).encode()).digest()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(data, sort_keys=True) + "\n").encode()
    if path.exists() and path.read_bytes() == encoded:
        return
    pending = path.with_suffix(".tmp")
    pending.write_bytes(encoded)
    pending.replace(path)


def export(target, source=None, parameter_roots=()):
    """Incremental, sanitized export. Content hashes protect every bound texture."""
    target, source = Path(target), Path(source or assembly())
    rows = legacy_rows(source)
    index = {}
    for key, row in sorted(rows.items()):
        for f in row["facades"]:
            meta = f["metadata"]
            directory = source / "facades" / key
            output = target / "facades" / key
            output.mkdir(parents=True, exist_ok=True)
            for name in meta["files"].values():
                if Path(name).name != name:
                    raise ValueError(f"unsafe facade filename: {name}")
                src, dest = directory / name, output / name
                if hashlib.sha256(src.read_bytes()).hexdigest() != meta["hashes"][name]:
                    raise ValueError(f"facade map hash mismatch: {src}")
                if not dest.exists() or dest.read_bytes() != src.read_bytes():
                    shutil.copy2(src, dest)
            _write(output / f"{meta['facade_index']}.json", meta)
        _write(target / "buildings" / opaque(key), row)
        candidates = [
            f["metadata"] for f in row["facades"] if f["metadata"].get("fast_description")
        ]
        if candidates:
            best = max(candidates, key=lambda m: m["width_m"])
            _write(
                target / "facade-params" / opaque(key),
                dict(
                    building_key=key,
                    description=best["fast_description"]["description"],
                    width_m=best["width_m"],
                    engine="stored",
                    source="existing-generated-facade",
                ),
            )
        index[key] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    for directory in parameter_roots:
        for path in Path(directory).glob("*.json"):
            row = json.loads(path.read_text())
            if row.get("building_key") and row.get("description"):
                clean = {
                    k: row[k]
                    for k in ("building_key", "description", "width_m", "engine", "field_sources")
                    if k in row
                }
                _write(target / "facade-params" / opaque(row["building_key"]), clean)
    library = source / "library"
    for path in library.rglob("*"):
        if path.is_file() and path.suffix in (".ktx2", ".json"):
            dest = target / "library" / path.relative_to(library)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or dest.read_bytes() != path.read_bytes():
                shutil.copy2(path, dest)
    result = dict(format="turku3d-facade-assets", version=1, buildings=index)
    _write(target / "index.json", result)
    return result


def main():
    import argparse
    import time

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    before = {p: p.stat().st_mtime_ns for p in args.export.rglob("*") if p.is_file()}
    result = export(args.export, args.source, [C.data_root() / "facade-params"])
    changed = [
        p for p in args.export.rglob("*") if p.is_file() and before.get(p) != p.stat().st_mtime_ns
    ]
    print(
        json.dumps(
            dict(
                buildings=len(result["buildings"]),
                changed_files=len(changed),
                changed_bytes=sum(p.stat().st_size for p in changed),
                seconds=round(time.monotonic() - started, 3),
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
