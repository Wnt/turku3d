"""Carry approved photo maps across building wall reindexing and small shell changes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import re
import shutil
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from turku3d import config as C

from .streetview.facades import load_facades, save_facades

INVENTORY_NAME = "APPROVED_INVENTORY.geojson"


class PhotoBoundsError(ValueError):
    """A valid rebuilt wall cannot use the approved photo without extrapolation."""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def asset_path(facade, root):
    building = str(facade.props["building_id"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", building):
        raise ValueError(f"unsafe building ID: {building}")
    index = facade.props["facade_index"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError(f"invalid facade index: {index}")
    return root / "facades" / building / str(index)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


@contextmanager
def export_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".export.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def match_score(old, new):
    """Return overlap score only for the same physical, outward-facing wall."""
    if old.props.get("building_id") != new.props.get("building_id"):
        return None
    if float(old.normal @ new.normal) < math.cos(math.radians(12)):
        return None
    tangent = old.tangent
    normal = old.normal
    offsets = [float((point - old.p0) @ normal) for point in (new.p0, new.p1)]
    if max(map(abs, offsets)) > 0.6 or abs(offsets[0] - offsets[1]) > 0.3:
        return None
    a, b = sorted(float((point - old.p0) @ tangent) for point in (new.p0, new.p1))
    overlap = max(0.0, min(old.length, b) - max(0.0, a))
    extension = min(4.0, max(0.6, old.length * 0.15))
    if overlap / new.length < 0.85 or min(a, old.length - b) < -extension:
        return None
    old_base, new_base = old.base_h, new.base_h
    if old_base is None or new_base is None:
        return None
    base_shift = abs(old_base - new_base)
    height_shift = abs(old.height_m - new.height_m)
    footing_extension = abs((old_base + old.height_m) - (new_base + new.height_m)) <= 0.01
    if base_shift > 0.75 + 1e-9 or (
        height_shift > min(5.0, old.height_m * 0.25)
        and not (footing_extension and height_shift <= 0.75 + 1e-9)
    ):
        return None
    return (
        overlap / new.length
        - 0.1 * max(map(abs, offsets))
        - 0.01 * abs(old.height_m - new.height_m)
    )


def reproject_uv(meta, old, new):
    """Map rebuilt metre UVs into the original photo, including split wall offsets."""
    if new.length > old.length * 1.1:
        raise PhotoBoundsError(f"{new.id}: rebuilt wall exceeds approved photo width")
    uv = dict(meta["uv"])
    scale = meta["uv"]["scale"]
    offset = meta["uv"]["offset"]
    horizontal = float(new.tangent @ old.tangent)
    if new.length > old.length * 1.05 and np.linalg.norm(new.centre - old.centre) < min(
        4.0, max(0.6, old.length * 0.15)
    ):
        horizontal *= old.length / new.length
    uv["scale"] = [scale[0] * horizontal, scale[1] * old.height_m / new.height_m]
    uv["offset"] = [
        offset[0] + scale[0] * float((new.p0 - old.p0) @ old.tangent),
        offset[1],
    ]
    horizontal_range = sorted((uv["offset"][0], uv["offset"][0] + uv["scale"][0] * new.length))
    if horizontal_range[1] - horizontal_range[0] > 1:
        uv["scale"][0] /= horizontal_range[1] - horizontal_range[0]
        uv["offset"][0] = 0 if uv["scale"][0] > 0 else 1
    elif horizontal_range[0] < 0:
        uv["offset"][0] -= horizontal_range[0]
    elif horizontal_range[1] > 1:
        uv["offset"][0] -= horizontal_range[1] - 1
    return uv


def validate_uv(uv, facade):
    for axis, extent in enumerate((facade.length, facade.height_m)):
        bounds = (uv["offset"][axis], uv["offset"][axis] + uv["scale"][axis] * extent)
        if not all(math.isfinite(value) for value in bounds):
            raise ValueError(f"{facade.id}: non-finite reprojected UV")
        if not all(-0.02 <= value <= 1.02 for value in bounds):
            raise PhotoBoundsError(f"{facade.id}: reprojected UV exceeds approved photo bounds")


def plan(source_inventory, inventory, assembly):
    old = load_facades(source_inventory)
    new = load_facades(inventory)
    manifest = json.loads((assembly / "MANIFEST.json").read_text())
    old_by_building = defaultdict(list)
    approved = defaultdict(list)
    for facade in old:
        old_by_building[facade.props["building_id"]].append(facade)
        if facade.id in manifest["bound"]:
            approved[facade.props["building_id"]].append(facade)
    matched, unmatched, ambiguous = [], [], []
    for facade in new:
        scored = [
            (score, candidate)
            for candidate in approved[facade.props["building_id"]]
            if (score := match_score(candidate, facade)) is not None
        ]
        if not scored:
            previously_present = any(
                match_score(candidate, facade) is not None
                for candidate in old_by_building[facade.props["building_id"]]
            )
            if facade.props.get("street_facing") and not previously_present:
                unmatched.append(facade)
            continue
        scored.sort(key=lambda item: item[0], reverse=True)
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.03:
            ambiguous.append(facade.id)
            continue
        matched.append((scored[0][1], facade))
    reused = {old.id for old, _ in matched}
    retired = sorted(
        facade.id for facades in approved.values() for facade in facades if facade.id not in reused
    )
    unmatched.sort(key=lambda facade: np.linalg.norm(facade.centre - (C.OFFICE_E, C.OFFICE_N)))
    return matched, unmatched, sorted(set(ambiguous) | set(retired))


def migrate(source_inventory, inventory, assembly):
    """Validate every source map, then replace bindings under the export lock."""
    with export_lock(assembly):
        source_hash = sha(source_inventory)
        manifest_path = assembly / "MANIFEST.json"
        manifest = json.loads(manifest_path.read_text())
        if manifest["inventory_sha256"] != source_hash:
            raise ValueError("approved source inventory hash mismatch")
        matched, unmatched, failed = plan(source_inventory, inventory, assembly)
        with tempfile.TemporaryDirectory(dir=assembly.parent, prefix="facade-rebind-") as temp:
            stage = Path(temp) / "facades"
            stage.mkdir()
            kept = reprojected = 0
            bindings = []
            provenance = []
            rejected = []
            for old, new in matched:
                origin = asset_path(old, assembly)
                meta = json.loads(origin.with_suffix(".json").read_text())
                validate_uv(meta["uv"], old)
                for name in meta["files"].values():
                    source = origin.parent / name
                    if sha(source) != meta["hashes"][name]:
                        raise ValueError(f"approved map hash mismatch: {source}")
                unchanged = (
                    old.id == new.id
                    and np.allclose(old.p0, new.p0, rtol=0, atol=0.001)
                    and np.allclose(old.p1, new.p1, rtol=0, atol=0.001)
                    and abs(old.base_h - new.base_h) <= 0.001
                    and abs(old.height_m - new.height_m) <= 0.001
                )
                # An unchanged wall keeps its approved mapping bit for bit: reprojecting it onto
                # itself drifts the last bits and would repack its tile on every rebind.
                try:
                    uv = meta["uv"] if unchanged else reproject_uv(meta, old, new)
                    validate_uv(uv, new)
                except PhotoBoundsError as error:
                    rejected.append({"old": old.id, "new": new.id, "reason": str(error)})
                    if new.props.get("street_facing"):
                        unmatched.append(new)
                    continue
                target = asset_path(new, Path(temp))
                target.parent.mkdir(parents=True, exist_ok=True)
                files = {}
                for kind, name in meta["files"].items():
                    source = origin.parent / name
                    suffix = ".ktx2" if kind == "albedo" else f".{kind}.ktx2"
                    destination = target.parent / (target.name + suffix)
                    shutil.copy2(source, destination)
                    files[kind] = destination.name
                meta.update(
                    facade_id=new.id,
                    building_id=new.props["building_id"],
                    facade_index=new.props["facade_index"],
                    width_m=new.length,
                    height_m=new.height_m,
                    uv=uv,
                    files=files,
                    hashes={name: sha(target.parent / name) for name in files.values()},
                )
                if unchanged:
                    kept += 1
                else:
                    reprojected += 1
                    meta["reprojected_from"] = old.id
                write_json(target.with_suffix(".json"), meta)
                bindings.append(new.id)
                provenance.append(
                    {"old": old.id, "new": new.id, "status": "kept" if unchanged else "reprojected"}
                )
            reused_ids = {item["old"] for item in provenance}
            failed = sorted(
                set(failed) | {item["old"] for item in rejected if item["old"] not in reused_ids}
            )
            unmatched.sort(
                key=lambda facade: np.linalg.norm(facade.centre - (C.OFFICE_E, C.OFFICE_N))
            )
            archive = assembly / "retired" / source_hash[:16]
            for identity in failed:
                if identity not in manifest["bound"]:
                    continue
                building, index = identity.rsplit("_", 1)
                source_dir = assembly / "facades" / building
                target_dir = archive / building
                target_dir.mkdir(parents=True, exist_ok=True)
                for source in source_dir.glob(index + ".*"):
                    shutil.copy2(source, target_dir / source.name)
            retired = assembly / "facades.retired"
            if retired.exists():
                shutil.rmtree(retired)
            current = assembly / "facades"
            current.rename(retired)
            stage.rename(current)
            manifest.update(inventory_sha256=sha(inventory), bound=sorted(bindings))
            write_json(manifest_path, manifest)
            shutil.copy2(inventory, assembly / INVENTORY_NAME)
            shutil.rmtree(retired)
    return {
        "kept": kept,
        "reprojected": reprojected,
        "new": len(unmatched),
        "new_ids": [facade.id for facade in unmatched],
        "failed": len(failed),
        "failed_ids": failed,
        "rejected": rejected,
        "matches": provenance,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--assembly", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--new-inventory", type=Path)
    args = parser.parse_args(argv)
    result = migrate(args.source_inventory, args.inventory, args.assembly)
    if args.new_inventory:
        selected = set(result["new_ids"])
        facades = [facade for facade in load_facades(args.inventory) if facade.id in selected]
        facades.sort(key=lambda facade: np.linalg.norm(facade.centre - (C.OFFICE_E, C.OFFICE_N)))
        save_facades(facades, args.new_inventory)
    if args.report:
        write_json(args.report, result)
    print(json.dumps({key: value for key, value in result.items() if key != "matches"}))


if __name__ == "__main__":
    main()
