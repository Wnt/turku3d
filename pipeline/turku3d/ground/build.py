"""Ground-layer build entry point. Never writes shipped .col/tileset artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely

from turku3d import config as C
from turku3d.bridges.build import crossings as bridge_crossings
from turku3d.col.materials import mat_id, table
from turku3d.layers import CollisionLayer, write_collision, write_manifest, write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.package.manifest import _atomic_write

from .breaklines import clamp_curb_heights, load_breaklines, records, validation_geojson
from .ground_qa import evaluate_ground
from .inputs import fingerprints, read_lidar, source_paths, vectors
from .materials import Materials
from .mesh import Mesh, build_mesh, parts, tile_mesh
from .points import Surface, select_ground, split_points
from .qa import write_report
from .ramps import classify_ramps
from .water import add_river_bed


def log(message):
    print(message, flush=True)


def code_hash():
    files = sorted(Path(__file__).parent.glob("*.py")) + [
        Path(C.__file__),
        Path(__file__).parents[1] / "package/gltf.py",
        Path(__file__).parents[1] / "col/materials_v1.json",
        Path(__file__).parents[1] / "layers.py",
        Path(__file__).parents[1] / "package/manifest.py",
        Path(__file__).parents[1] / "bridges/build.py",
    ]
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def visual_meshes(mesh, tile, curbs):
    out = []
    selected = tile_mesh(mesh, tile)
    for material in np.unique(selected.material):
        # Raised curb collision strips are replaced by the visual bevel below.
        if material == mat_id("curb"):
            continue
        part = Mesh(
            selected.positions,
            selected.indices[selected.material == material],
            selected.material[selected.material == material],
        ).compact()
        name = table()[int(material)].name
        object_id = f"ground:{tile.id}:{name}"
        out.append(
            GlbMesh(
                object_id,
                part.positions,
                part.indices,
                uvs=part.positions[:, [0, 2]] * [1, -1],
                extras={
                    "layer": "ground",
                    "object_id": object_id,
                    "material_key": name,
                    "sensor": name == "water",
                },
            )
        )
    envelope = shapely.box(*tile.bbox.as_tuple())
    for curb in curbs:
        if curb.kind != "curb":
            continue
        left, right = curb.lines()
        positions, faces = [], []
        for i in range(len(left) - 1):
            cross_sections = []
            low_side, high_side = (
                (left, right)
                if left[i : i + 2, 2].mean() < right[i : i + 2, 2].mean()
                else (right, left)
            )
            for j in (i, i + 1):
                low, high = low_side[j], high_side[j]
                bevel = high.copy()
                bevel[:2] = high[:2] * 0.75 + low[:2] * 0.25
                bevel[2] -= max(0.0, min(0.015, (high[2] - low[2]) / 3))
                cross_sections.append(np.array([low, bevel, high]))
            a, b = cross_sections
            for k in (0, 1):
                polygon = shapely.Polygon([a[k], b[k], b[k + 1], a[k + 1]])
                if not polygon.is_valid or polygon.area < 1e-12:
                    continue
                for part in parts(polygon.intersection(envelope), ("Polygon",)):
                    xyz = np.asarray(part.exterior.coords)[:-1][:, [0, 2, 1]]
                    start = len(positions)
                    positions.extend(xyz)
                    for v in range(1, len(xyz) - 1):
                        face = [start, start + v, start + v + 1]
                        tri = np.array([positions[q] for q in face])
                        if np.cross(tri[1] - tri[0], tri[2] - tri[0])[1] < 0:
                            face.reverse()
                        faces.append(face)
        if faces:
            p = np.asarray(positions)
            object_id = f"curb:{curb.object_id}"
            out.append(
                GlbMesh(
                    object_id,
                    p,
                    np.asarray(faces, np.uint32),
                    uvs=p[:, [0, 2]] * [1, -1],
                    extras={
                        "layer": "ground",
                        "object_id": object_id,
                        "material_key": "curb",
                        "visual_only_bevel": True,
                    },
                )
            )
    return out


PARAMS = {
    "margin": C.MESH_MARGIN,
    "adaptive_tolerance": 0.02,
    "station_spacing": 1.0,
    "breakline_half_width": 0.03,
}
# The Aura has one level for every tile, measured once over the office core.
WATER_REFERENCE = C.tile_area("water-reference", "office core", C.CORE_TILES).core


def envelope(tile):
    """A tile's own collision tiles plus one ring, and the lidar bounds that support them.

    Constraint segments close every collision tile, so a tile's triangulation depends only on
    the samples inside it; any envelope that contains a tile with this margin yields the same
    tile. Neighbouring tiles built separately therefore share their seams exactly."""
    cells = C.collision_tiles_for_bbox(tile.bbox.buffered(C.MESH_MARGIN))
    cell_bounds = C.BBoxLocal(
        min(c.bbox.xmin for c in cells),
        min(c.bbox.zmin for c in cells),
        max(c.bbox.xmax for c in cells),
        max(c.bbox.zmax for c in cells),
    )
    return cells, cell_bounds.buffered(C.MESH_MARGIN)


def tile_paths(bounds, source_area, breaklines=None):
    paths = source_paths(bounds, source_area)
    # Track the VRT's referenced TIFFs as well as its XML (pixel changes invalidate tiles).
    import xml.etree.ElementTree as ET

    for i, node in enumerate(ET.parse(paths["ortho"]).findall(".//SourceFilename")):
        path = Path(node.text)
        if not path.is_absolute():
            path = paths["ortho"].parent / path
        paths[f"ortho_chunk_{i}"] = path.resolve()
    if breaklines:
        paths["breaklines"] = Path(breaklines).resolve()
    return paths


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def water_reference(source_area, override):
    """Identity of the water level every tile uses; the value is measured only when needed."""
    if override is not None:
        return {"key": digest(["override", override])}
    paths = source_paths(WATER_REFERENCE, source_area)
    identity = fingerprints(
        {k: v for k, v in paths.items() if k.endswith("osm") or ".laz" in str(v)}
    )
    return {
        "key": digest([{k: v["sha256"] for k, v in identity.items()}, repr(WATER_REFERENCE)]),
        "paths": paths,
    }


def measure_water_level(reference, override):
    if override is not None:
        return override, "explicit N2000 level override"
    paths = reference["paths"]
    _, water, *_ = vectors(paths, WATER_REFERENCE)
    if water.is_empty:
        return 0.0, "no water polygons over the office core"
    points, classes, heights = read_lidar(paths, WATER_REFERENCE)
    if len(heights) >= 20:
        return float(np.median(heights)), "median class-9 lidar over the office core"
    (training, _), _ = split_points(points, classes)
    wet_points = training[shapely.contains_xy(water.buffer(-3), training[:, 0], training[:, 2])]
    if len(wet_points) < 20:
        raise ValueError(
            "No reliable water returns: provide --water-level N2000_METRES; no implicit sea-level guess"
        )
    # Water returns can be labelled overlap/ground in this source. Lowest modal 5 cm
    # height bin excludes bridge decks; require adequate support and report provenance.
    bins, counts = np.unique(np.rint(wet_points[:, 1] / 0.05).astype(int), return_counts=True)
    significant = counts >= max(20, counts.max() * 0.1)
    level_bin = bins[significant].min()
    sample = wet_points[np.abs(wet_points[:, 1] - level_bin * 0.05) <= 0.1, 1]
    return (
        float(np.median(sample)),
        f"lowest supported river-interior lidar mode ({len(sample)} returns)",
    )


class WaterLevel:
    """Measured once per build, and only when a rebuilt tile has water; reused by key."""

    def __init__(self, source_area, override, previous):
        self.override = override
        self.reference = water_reference(source_area, override)
        self.key = self.reference["key"]
        self.value = previous if previous and previous.get("key") == self.key else None

    def __call__(self):
        if self.value is None:
            level, source = measure_water_level(self.reference, self.override)
            self.value = {"key": self.key, "metres_n2000": level, "source": source}
        return self.value["metres_n2000"], self.value["source"]


def build_tile(tile, paths, inputs, key, curbs, water_level, staging):
    """Mesh one L0 tile in its own envelope; write its own and ring collision tiles to staging."""
    started = time.monotonic()
    cells, bounds = envelope(tile)
    cache = C.cache_dir("ground")
    raw_key = hashlib.sha256(
        json.dumps(
            {k: v for k, v in inputs.items() if v["path"].endswith(".laz")}, sort_keys=True
        ).encode()
        + repr(bounds).encode()
    ).hexdigest()
    raw_cache = cache / f"lidar-{raw_key}.npz"
    if not raw_cache.exists():
        log(f"{tile.id}: reading class-2/12 lidar (read-only raw inputs)")
        points, classes, _water_heights = read_lidar(paths, bounds)
        temporary = raw_cache.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            np.savez(stream, points=points, classes=classes)
        temporary.replace(raw_cache)
        del points, classes
    lines, water, buildings, _crossings, streets, ramp_lines = vectors(paths, bounds)
    bridges = (
        shapely.union_all(
            [
                crossing.footprint
                for crossing in bridge_crossings(streets, gpd.read_parquet(paths["osm"]))
            ]
        )
        if streets is not None
        else shapely.Polygon()
    )
    selection_key = hashlib.sha256(
        (raw_key + Path(__file__).with_name("points.py").read_text() + water.wkb_hex).encode()
    ).hexdigest()
    selection_cache = cache / f"selection-{selection_key}.npz"
    if selection_cache.exists():
        with np.load(selection_cache, allow_pickle=False) as data:
            accepted = data["accepted"]
            selection = json.loads(str(data["stats"]))
    else:
        log(f"{tile.id}: selecting and denoising ground points")
        with np.load(raw_cache) as data:
            (training, train_cls), (heldout, held_cls) = split_points(
                data["points"], data["classes"]
            )
        accepted, selection = select_ground(training, train_cls)
        accepted = accepted[~shapely.contains_xy(water, accepted[:, 0], accepted[:, 2])]
        # Held-out class 2 is never residual-trimmed. Class 12 eligibility uses TRAINING class 2.
        reference = Surface(training[train_cls == 2])
        h, _ = reference.sample(heldout[:, [0, 2]])
        heldout = heldout[
            (held_cls == 2) | ((held_cls == 12) & (np.abs(heldout[:, 1] - h) <= 0.15))
        ]
        np.savez(selection_cache, accepted=accepted, heldout=heldout, stats=json.dumps(selection))
    water_level, water_source = water_level() if not water.is_empty else (0.0, "no water")
    log(f"{tile.id}: ground selection {selection}; water level {water_level:.3f} ({water_source})")
    curb_key = hashlib.sha256(json.dumps(records(curbs), sort_keys=True).encode()).hexdigest()
    materials = Materials(streets, paths["ortho"])
    mesh_key = hashlib.sha256(
        (
            selection_key
            + curb_key
            + str(water_level)
            + repr(PARAMS)
            + repr(bounds)
            + Path(__file__).with_name("mesh.py").read_text()
            + Path(__file__).with_name("materials.py").read_text()
            + bridges.wkb_hex
            + json.dumps({k: v for k, v in inputs.items() if k != "kanta_Rakenne"}, sort_keys=True)
        ).encode()
    ).hexdigest()
    mesh_cache = cache / f"mesh-{mesh_key}.npz"
    info_cache = cache / f"mesh-{mesh_key}.json"
    if mesh_cache.exists() and info_cache.exists():
        mesh = Mesh.load(mesh_cache)
        mesh_info = json.loads(info_cache.read_text())
        log(f"{tile.id}: using cached CDT")
    else:
        log(f"{tile.id}: building adaptive CDT")
        mesh, mesh_info = build_mesh(
            Surface(accepted),
            bounds,
            cells,
            curbs,
            water,
            water_level,
            materials,
            tolerance=PARAMS["adaptive_tolerance"],
            bridges=bridges,
        )
        mesh.save(mesh_cache)
        info_cache.write_text(json.dumps(mesh_info))
    ramp_coverage, ramp_records = classify_ramps(mesh, ramp_lines)
    visual_source = mesh
    mesh = add_river_bed(mesh, water_level)
    meshes = {cell: tile_mesh(mesh, cell) for cell in cells}
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for cell, part in meshes.items():
        write_collision(
            staging / f"{cell.id}.col.npz",
            CollisionLayer(part.positions, part.indices, part.material),
        )
    write_visual(
        staging / f"{tile.id}.vis.glb",
        visual_meshes(visual_source, tile, curbs),
        root_extras={"layer": "ground", "tile": tile.id},
    )
    report = evaluate_ground(
        {cell: meshes[cell] for cell in tile.collision_tiles()},
        accepted,
        water,
        buildings,
        tile.bbox,
        bridges.intersection(water.buffer(2.0)),
    )
    report.update(
        {
            "selection": selection,
            "mesh_build": mesh_info,
            "ramp_coverage": ramp_coverage,
            "source_lines": len(lines),
            "water_level": {"metres_n2000": water_level, "source": water_source},
            "build_seconds": round(time.monotonic() - started, 3),
        }
    )
    return {
        "key": key,
        "artifacts": {p.name: sha(p) for p in sorted(staging.iterdir())},
        "passed": report["passed"],
        "checks": report["checks"],
        "ramps": ramp_records,
        "report": report,
    }


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owners(visual):
    """Which tile's build publishes each layer file: its own tiles, and margin tiles of unlisted
    neighbours from the first listed tile whose envelope holds them (equal by construction)."""
    listed = set(visual)
    result = {}
    for tile in sorted(visual):
        result[f"{tile.id}.vis.glb"] = tile
        for cell in envelope(tile)[0]:
            name = f"{cell.id}.col.npz"
            if cell.visual_parent() == tile or (
                cell.visual_parent() not in listed and name not in result
            ):
                result[name] = tile
    return result


def _build_one(job):
    tile_id, paths, inputs, key, breaklines, water, staging = job
    curbs = clamp_curb_heights(load_breaklines(breaklines)) if breaklines else []
    return build_tile(C.VisualTile.parse(tile_id), paths, inputs, key, curbs, water, staging)


def _write_if_changed(path, data: bytes):
    if not path.is_file() or path.read_bytes() != data:
        _atomic_write(path, data)


def run(args):
    started = time.monotonic()
    visual = sorted({C.VisualTile.parse(t) for t in args.tiles})
    if any(t.level != 0 for t in visual):
        raise ValueError("Ground builds accept only L0 tiles")
    output = C.work_root() / "build/layers/ground"
    reports = C.work_root() / "reports/ground"
    stagings = C.cache_dir("ground") / "tiles"
    manifest_path = output / "MANIFEST.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    breaklines = getattr(args, "breaklines", None)
    water = WaterLevel(args.source_area, args.water_level, previous.get("water_level"))
    code = code_hash()
    plans = {}
    for tile in visual:
        paths = tile_paths(envelope(tile)[1], args.source_area, breaklines)
        inputs = fingerprints(paths)
        key = digest(
            {
                "tile": tile.id,
                "inputs": {k: v["sha256"] for k, v in inputs.items()},
                "params": PARAMS,
                "code": code,
                "water": water.key,
            }
        )
        plans[tile] = (paths, inputs, key)
    builds = {
        tile.id: record
        for tile in visual
        if (record := previous.get("tile_builds", {}).get(tile.id))
        and record.get("key") == plans[tile][2]
    }
    owned = owners(visual)

    def available(name, tile):
        """The output already holds this file, or the tile's staging can restore it."""
        record = builds.get(tile.id)
        if record is None or name not in record["artifacts"]:
            return False
        expected = record["artifacts"][name]
        return any(
            p.is_file() and sha(p) == expected for p in (output / name, stagings / tile.id / name)
        )

    stale = sorted({tile for name, tile in owned.items() if not available(name, tile)})
    built = {}
    if stale:
        jobs = [
            (
                tile.id,
                *plans[tile],
                breaklines,
                water,
                stagings / tile.id,
            )
            for tile in stale
        ]
        workers = min(len(jobs), max(1, int(os.environ.get("TURKU3D_JOBS", "1"))))
        log(f"Ground: building {', '.join(t.id for t in stale)} ({workers} at a time)")
        if workers == 1:
            results = map(_build_one, jobs)
        else:
            water()  # Measure once here instead of once per worker.
            pool = ProcessPoolExecutor(max_workers=workers)
            results = pool.map(_build_one, jobs)
        for tile, record in zip(stale, results, strict=True):
            built[tile.id] = record
            reports.mkdir(parents=True, exist_ok=True)
            (reports / f"{tile.id}.json").write_text(
                json.dumps(record.pop("report"), indent=2, allow_nan=False) + "\n"
            )
        if workers > 1:
            pool.shutdown()
    builds.update(built)
    output.mkdir(parents=True, exist_ok=True)
    for name, tile in owned.items():
        target, source = output / name, stagings / tile.id / name
        expected = builds[tile.id]["artifacts"][name]
        if target.is_file() and sha(target) == expected:
            continue
        _atomic_write(target, source.read_bytes())
    for path in output.iterdir():
        if path.name.endswith((".col.npz", ".vis.glb")) and path.name not in owned:
            path.unlink()
    curbs = clamp_curb_heights(load_breaklines(breaklines)) if breaklines else []
    ramps = {}
    for tile in visual:
        for record in builds[tile.id]["ramps"]:
            best = ramps.get(record["object_id"])
            if best is None or record["covered_length_m"] > best["covered_length_m"]:
                ramps[record["object_id"]] = record
    side_files = {
        # Quays are a handoff, never disguised as a curb collider.
        "quays.json": records([c for c in curbs if c.kind == "quay"]),
        "breaklines.json": records(curbs),
        "curbs.geojson": validation_geojson(curbs),
        "ramps.json": [ramps[k] for k in sorted(ramps)],
    }
    for name, data in side_files.items():
        _write_if_changed(output / name, (json.dumps(data, indent=2) + "\n").encode())
    checks = {}
    for tile in visual:
        for check, value in builds[tile.id]["checks"].items():
            checks[check] = checks.get(check, True) and value
    passed = all(builds[t.id]["passed"] for t in visual)
    tile_builds = {t.id: builds[t.id] for t in visual}
    layer_key = digest([tile_builds, sorted(side_files)])
    if (
        previous.get("cache_key") == layer_key
        and not built
        and all(sha(output / f) == d for f, d in previous.get("artifacts", {}).items())
    ):
        log(json.dumps({"cached": True, "tiles": len(visual), "acceptance_passed": passed}))
        return 0 if passed else 2
    report = {
        "passed": passed,
        "checks": checks,
        "metrics": {t.id: f"{t.id}.json" for t in visual},
        "acceptance": "Plain lidar ground per L0 tile; curb accuracy deferred by user decision",
        "rebuilt": sorted(built),
        "water_level": water.value,
        "river_bed": {
            "source": "synthetic collision fallback; no measured bathymetry",
            "depth_below_water_m": 3.0,
            "visual": False,
        },
        "build_seconds": time.monotonic() - started,
    }
    write_report(report, reports)
    inputs = {}
    for tile in visual:
        inputs.update(plans[tile][1])
    names = sorted(owned) + sorted(side_files)
    manifest = write_manifest(
        output,
        inputs=inputs,
        params={
            **PARAMS,
            "visual_tiles": [t.id for t in visual],
            "water_level_override": args.water_level,
        },
        code=code,
        tiles=sorted(owned),
    )
    manifest.update(
        {
            "code_version": code,
            "river_bed": report["river_bed"],
            "validation": {"curbs": "curbs.geojson"},
            "schema_version": 2,
            "cache_key": layer_key,
            "water_level": water.value,
            "tile_builds": tile_builds,
            "artifacts": {name: sha(output / name) for name in names},
            "qa": str(reports / "ground-qa.json"),
            "acceptance_passed": passed,
        }
    )
    _atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    log(
        json.dumps(
            {
                "checks": checks,
                "rebuilt": sorted(built),
                "seconds": round(time.monotonic() - started, 2),
                "output": str(output),
            },
            indent=2,
        )
    )
    return 0 if passed else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+", default=list(C.CORE_TILES))
    parser.add_argument(
        "--breaklines", type=Path, help="Measured breakline JSON; matching IDs replace city fits"
    )
    parser.add_argument("--source-area", default="work-3x3")
    parser.add_argument(
        "--water-level",
        type=float,
        help="Explicit N2000 water elevation when no reliable lidar returns exist",
    )
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
