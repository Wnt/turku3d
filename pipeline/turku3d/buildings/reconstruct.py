"""Per-building reconstruction and shell selection, reused from the layer while keys match.

A building's reconstruction key covers its ID, its footprint, the lidar tiles under it and the
roofer code and version. Its selection key adds the city model matched to it, its properties,
the lidar tiles within the selection's 100 m evidence reach and the selection code. The layer
keeps both products (`reconstruction.jsonl`, `shells.json` with `shells.npz`), so a rebuild
for a new tile runs roofer and selection only for buildings whose keys changed. A changed
reconstruction code or roofer version changes every key and reconstructs the whole list.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from shapely.ops import unary_union

from turku3d import config as C
from turku3d.region.cache import atomic_target, cache_lock

from . import roofer
from .fallback import extrusion, lidar_extrusion, placeholder_extrusion
from .height import overlap_points
from .inputs import read_points, write_roofer_footprints
from .select import choose
from .shells import Shell, Surface

RECONSTRUCTIONS = "reconstruction.jsonl"
SHELLS = "shells.json"
SHELL_ARRAYS = "shells.npz"
# Roofer's terrain fallback searches three 10 m cells around a footprint. Neighbours within
# this reach ride along in a changed building's run, as they would in a whole-list run.
CONTEXT_M = 30.0
# Selection reads class-6 points up to 100 m around a footprint (fallback height evidence).
EVIDENCE_M = 100.0
_HERE = Path(__file__).parent
SELECTION_CODE = [
    *(
        _HERE / name
        for name in (
            "citygml.py",
            "distance.py",
            "fallback.py",
            "height.py",
            "inputs.py",
            "reconstruct.py",
            "select.py",
            "shells.py",
        )
    ),
    _HERE.parent / "col/format.py",
    _HERE.parent / "col/materials.py",
    _HERE.parent / "col/materials_v1.json",
]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _code(paths) -> str:
    return _digest([[p.name, sha256(p)] for p in paths])


@functools.cache
def reconstruction_code() -> str:
    return _digest([_code([Path(roofer.__file__)]), roofer.version()])


@functools.cache
def selection_code() -> str:
    return _code(SELECTION_CODE)


def _laz(bounds) -> list[Path]:
    tiles = sorted(C.laz_tiles_for_bbox(bounds), key=lambda t: t.name)
    return [C.raw_dir("laz") / "pistepilvi" / (t.name + ".laz") for t in tiles]


def roofer_lidar(building) -> list[Path]:
    """The LAZ tiles roofer reads for a building: all of them, or the run fails loudly."""
    return _laz(C.BBoxLocal(*building.footprint.bounds).buffered(5))


def evidence_lidar(building) -> list[Path]:
    """The present LAZ tiles within the selection's evidence reach of a building."""
    return [
        p for p in _laz(C.BBoxLocal(*building.footprint.bounds).buffered(EVIDENCE_M)) if p.is_file()
    ]


def identity(paths):
    return [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in paths]


def reconstruction_key(building) -> str:
    return _digest(
        [
            reconstruction_code(),
            building.id,
            building.footprint.wkb_hex,
            identity(roofer_lidar(building)),
        ]
    )


def selection_key(building, reconstruction, city, match) -> str:
    h = hashlib.sha256()
    for surface in city:
        h.update(surface.kind.encode())
        for ring in surface.rings:
            h.update(np.ascontiguousarray(ring, np.float64).tobytes())
    return _digest(
        [
            selection_code(),
            reconstruction,
            json.loads(json.dumps(building.properties, sort_keys=True, default=str)),
            h.hexdigest(),
            match,
            identity(evidence_lidar(building)),
        ]
    )


def read_store(layer: Path):
    """The previous build's per-building reconstructions and selections, keyed by building."""
    reconstructions, shells = {}, {}
    if (layer / RECONSTRUCTIONS).is_file():
        for line in (layer / RECONSTRUCTIONS).read_text().splitlines():
            entry = json.loads(line)
            reconstructions[entry["building_id"]] = entry
    if (layer / SHELLS).is_file() and (layer / SHELL_ARRAYS).is_file():
        with np.load(layer / SHELL_ARRAYS, allow_pickle=False) as arrays:
            for index, entry in enumerate(json.loads((layer / SHELLS).read_text())["buildings"]):
                if entry["shell"]:
                    entry["arrays"] = {k: arrays[f"{k}{index}"] for k in "ptms"}
                shells[entry["building_id"]] = entry
    return reconstructions, shells


def store_files(buildings, reconstructions, selections) -> dict[str, bytes]:
    """The layer files that let the next build reuse each building's products."""
    lines = "".join(
        json.dumps(reconstructions[b.id], sort_keys=True, separators=(",", ":")) + "\n"
        for b in buildings
    )
    entries, arrays = [], {}
    for index, b in enumerate(buildings):
        key, shell, record = selections[b.id]
        entries.append(
            {
                "building_id": b.id,
                "key": key,
                "shell": shell is not None,
                "kinds": [s.kind for s in shell.surfaces] if shell is not None else [],
                "hausdorff": shell.hausdorff if shell is not None else None,
                "record": record,
            }
        )
        if shell is not None:
            arrays.update(
                {
                    f"p{index}": shell.positions,
                    f"t{index}": shell.indices,
                    f"m{index}": shell.material,
                    f"s{index}": shell.surface_ids,
                }
            )
    stream = io.BytesIO()
    np.savez_compressed(stream, **arrays)
    document = {"schema_version": 1, "buildings": entries}
    return {
        RECONSTRUCTIONS: lines.encode(),
        SHELLS: (json.dumps(document, sort_keys=True, allow_nan=False) + "\n").encode(),
        SHELL_ARRAYS: stream.getvalue(),
    }


def _stored_shell(entry, footprint) -> Shell:
    """Publishing reads triangles and surface kinds; source rings are not stored."""
    a = entry["arrays"]
    surfaces = [Surface([], kind) for kind in entry["kinds"]]
    return Shell(a["p"], a["t"], a["m"], a["s"], surfaces, footprint, entry["hausdorff"])


def run_roofer(batch, cache: Path, jobs: int):
    """Roofer over one footprint batch, cached by the batch in the shared data cache."""
    laz = sorted({p for b in batch for p in roofer_lidar(b)})
    key = _digest(
        [reconstruction_code(), [(b.id, b.footprint.wkb_hex) for b in batch], identity(laz)]
    )[:20]
    output = cache / "roofer" / key
    with cache_lock(cache / "locks" / f"roofer-{key}"):
        if not (output / "execution.json").exists():
            footprints = cache / "footprints" / f"{key}.gpkg"
            if not footprints.exists():
                with atomic_target(footprints) as temporary:
                    write_roofer_footprints(batch, temporary)
            roofer.run(laz, footprints, output, jobs)
    return roofer.features(output.glob("*.city.jsonl"))


def reconstruct_changed(changed, buildings, established, cache, jobs):
    """Roofer entries for changed buildings only.

    Established kantakartta buildings run among established neighbours only, so GIS
    gap fillers never move their geometry; GIS buildings run among all neighbours. Each
    run carries the neighbours within CONTEXT_M as context and keeps only the changed results.
    """
    entries = {}
    groups = [
        (
            [b for b in changed if b.id in established],
            [b for b in buildings if b.id in established],
        ),
        ([b for b in changed if b.id not in established], buildings),
    ]
    for targets, pool in groups:
        if not targets:
            continue
        reach = unary_union([b.footprint for b in targets]).buffer(CONTEXT_M)
        batch = [b for b in pool if b.footprint.intersects(reach)]
        print(f"Roofer: {len(targets)} changed buildings, {len(batch)} with context", flush=True)
        features = run_roofer(batch, cache, jobs)
        for b in targets:
            transform, feature = features.get(b.id, (None, None))
            entries[b.id] = {"transform": transform, "feature": feature}
    return entries


def class6(paths, cache: Path):
    """Class-6 points of whole LAZ tiles, cached per tile by content."""
    arrays = []
    for path in paths:
        key = sha256(path)
        target = cache / f"{key}.class6.npy"
        with cache_lock(cache / "locks" / key):
            if not target.exists():
                points = read_points([path], C.LazTile.parse(path.stem).bbox_en.to_local())
                with atomic_target(target) as temporary:
                    with temporary.open("wb") as stream:
                        np.save(stream, points)
        arrays.append(np.load(target, mmap_mode="r"))
    return np.concatenate(arrays) if arrays else np.empty((0, 3))


def _ground(bounds):
    from turku3d.lidar import TileStore

    store = TileStore(source_dir=C.raw_dir("laz") / "pistepilvi", cache_dir=C.cache_dir("lidar"))
    return store.query(bounds, classes=[2]).points


def select_shell(building, city, reconstructed, metadata, points, overlap):
    """Choose the best closed shell; fall back to lidar extrusions, then a placeholder."""
    x0, z0, x1, z1 = building.footprint.bounds
    padding = 100 if metadata.get("rf_pointcloud_unusable") else 20
    keep = (
        (points[:, 0] >= x0 - padding)
        & (points[:, 0] <= x1 + padding)
        & (points[:, 2] >= z0 - padding)
        & (points[:, 2] <= z1 + padding)
    )
    near = np.asarray(points[keep])
    shell, record = choose(building, {"city": city, "roofer": reconstructed}, near)
    if not record["pass"]:
        reason = "no_valid_reconstruction" if shell is None else "residual_limits_exceeded"
        try:
            surfaces, evidence = lidar_extrusion(building, near, metadata, overlap)
            alternatives = {"city": city, "roofer": reconstructed, "lidar_extrusion": surfaces}
            if evidence.get("roof_shape") != "flat":
                alternatives["lidar_flat"] = extrusion(
                    building.footprint, evidence["base_m"], evidence["roof_m"]
                )
            shell, record = choose(building, alternatives, near)
            record["fallback"] = {
                "reason": reason,
                "selected": record["choice"] in ("lidar_extrusion", "lidar_flat"),
                "height_evidence": evidence,
            }
        except ValueError as exc:
            record["fallback"] = {"reason": reason, "error": str(exc)}
    if shell is None:
        ground = None
        if metadata.get("rf_h_ground") is None:
            ground = _ground(C.BBoxLocal(x0 - 15, z0 - 15, x1 + 15, z1 + 15))
        surfaces, evidence = placeholder_extrusion(building, metadata, ground)
        shell, record = choose(building, {"placeholder": surfaces}, near)
        record["fallback"] = {
            "reason": "no_usable_model_or_lidar_shell",
            "selected": shell is not None,
            "height_evidence": evidence,
        }
    if building.properties.get("point_only"):
        ground = _ground(C.BBoxLocal(x0 - 30, z0 - 30, x1 + 30, z1 + 30))
        surfaces, evidence = placeholder_extrusion(building, {}, ground)
        shell, record = choose(building, {"placeholder": surfaces}, near)
        record["fallback"] = {
            "reason": "source_has_point_only_no_surveyed_outline",
            "selected": shell is not None,
            "height_evidence": evidence,
        }
    return shell, record


def reconstruct(buildings, established, city, matches, layer: Path, cache: Path, jobs: int):
    """(shells by building ID, records in building order, layer store files, counts)."""
    old_reconstructions, old_shells = read_store(layer)
    rkeys = {b.id: reconstruction_key(b) for b in buildings}
    skeys = {
        b.id: selection_key(b, rkeys[b.id], city.get(b.id, []), matches.get(b.id))
        for b in buildings
    }
    reconstructions = {
        b.id: old_reconstructions[b.id]
        for b in buildings
        if old_reconstructions.get(b.id, {}).get("key") == rkeys[b.id]
    }
    rerun = [b for b in buildings if b.id not in reconstructions]
    for bid, entry in reconstruct_changed(rerun, buildings, established, cache, jobs).items():
        reconstructions[bid] = {"building_id": bid, "key": rkeys[bid], **entry}
    reselect = [b for b in buildings if old_shells.get(b.id, {}).get("key") != skeys[b.id]]
    selections = {}
    for b in buildings:
        entry = old_shells.get(b.id)
        if entry is not None and entry["key"] == skeys[b.id]:
            shell = _stored_shell(entry, b.footprint) if entry["shell"] else None
            selections[b.id] = (skeys[b.id], shell, entry["record"])
    if reselect:
        laz = sorted({p for b in reselect for p in evidence_lidar(b)})
        points = class6(laz, cache)
        surfaces, metadata = {}, {}
        for b in reselect:
            entry = reconstructions[b.id]
            if entry["feature"] is not None:
                for bid, items in roofer.surfaces(entry["transform"], entry["feature"]).items():
                    surfaces.setdefault(bid, []).extend(items)
                metadata.update(roofer.metadata(entry["feature"]))
        missing = [b for b in reselect if metadata.get(b.id, {}).get("rf_pointcloud_unusable")]
        overlap = (
            overlap_points(laz, missing, cache, _digest(identity(laz)))
            if missing
            else np.empty((0, 3))
        )
        for i, b in enumerate(reselect):
            shell, record = select_shell(
                b,
                city.get(b.id, []),
                surfaces.get(b.id, []),
                metadata.get(b.id, {}),
                points,
                overlap,
            )
            record["shell_valid"] = shell is not None
            record["city_match"] = matches.get(b.id)
            record["roofer_metadata"] = metadata.get(b.id)
            selections[b.id] = (skeys[b.id], shell, record)
            print(
                f"{i + 1}/{len(reselect)} {b.id}: {record['choice']}, pass={record['pass']}",
                flush=True,
            )
    files = store_files(buildings, reconstructions, selections)
    shells = {bid: shell for bid, (_, shell, _) in selections.items() if shell is not None}
    # Publishing adds per-build fields to the records; the stored copies stay as selected.
    records = [copy.deepcopy(selections[b.id][2]) for b in buildings]
    counts = {"reconstructed": len(rerun), "selected": len(reselect), "buildings": len(buildings)}
    return shells, records, files, counts
