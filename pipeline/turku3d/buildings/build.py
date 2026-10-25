"""Build the buildings layer without writing shipped .col, GLB or tilesets."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import mapping
from shapely.ops import unary_union

from turku3d import config as C
from turku3d.layers import CollisionLayer, write_manifest, write_visual

from . import citygml, roofer
from .facades import make_visuals, ortho_roof_key
from .fallback import lidar_extrusion
from .height import overlap_points
from .inputs import (
    attach_osm,
    envelope,
    local_polygon,
    read_footprints,
    read_points,
    write_roofer_footprints,
)
from .select import choose
from .tiles import collision_tiles


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _publish(buildings, selected, records, output, streets, ortho_paths, inputs, params):
    """Failed quality remains explicit; products contain only valid closed shells."""
    output.mkdir(parents=True, exist_ok=True)
    visuals, features, files = defaultdict(list), [], []
    accepted = []
    for building, record in zip(buildings, records, strict=True):
        shell = selected.get(building.id)
        if shell is None:
            continue
        print(f"Writing surfaces: {building.id}", flush=True)
        key, color = ortho_roof_key(building, ortho_paths)
        record["roof"] = {"material_key": key, **color}
        record["semantics"] = building.properties
        meshes, facades = make_visuals(
            building, shell, streets, [b.footprint for b in buildings], key
        )
        # Whole objects are owned once by the tile containing their representative point.
        # Assembler uses actual mesh bounds for cross-tile culling.
        point = building.footprint.representative_point()
        tile = C.VisualTile.at(point.x, point.y)
        visuals[tile.id].extend(meshes)
        features.extend(facades)
        accepted.append(shell)
    footprints = [b.footprint for b in buildings if b.id in selected]
    for tid, arrays in collision_tiles(accepted, footprints, provenance=True).items():
        name = tid + ".col.npz"
        # Keep per-triangle ownership for isolated walker validation.
        CollisionLayer(arrays["positions"], arrays["indices"], arrays["material"]).validate()
        np.savez_compressed(output / name, **arrays)
        files.append(name)
    for tid, meshes in sorted(visuals.items()):
        name = tid + ".vis.glb"
        write_visual(output / name, meshes, root_extras={"layer": "buildings", "tile": tid})
        files.append(name)
    write_json(
        output / "facades.geojson",
        {
            "type": "FeatureCollection",
            "schema_version": 1,
            "turku3d_coords": "en",
            "crs": {"type": "name", "properties": {"name": C.CRS_HORIZONTAL}},
            "features": features,
        },
    )
    write_json(
        output / "footprints.geojson",
        {
            "type": "FeatureCollection",
            "turku3d_coords": "engine-xz",
            "features": [
                {
                    "type": "Feature",
                    "id": b.id,
                    "properties": {"id": b.id, "building_id": b.id, "has_shell": b.id in selected},
                    "geometry": mapping(b.footprint),
                }
                for b in buildings
            ],
        },
    )
    report = {
        "schema_version": 1,
        "building_count": len(buildings),
        "shell_count": len(selected),
        "facade_count": len(features),
        "shell_coverage_pass": len(selected) == len(buildings) > 0,
        "collision_building_ids": [b.id for b in buildings if b.id in selected],
        "fallback_count": sum(r.get("fallback") is not None for r in records),
        "pass": bool(buildings) and all(r["pass"] for r in records),
        "buildings": records,
        "params": params,
        "walker_validation": {
            "status": "not_run",
            "reason": "Run buildings-validate after layer build",
        },
    }
    write_json(output / "metrics.json", report)
    errors = []
    lines = [
        "# Buildings layer metrics",
        "",
        f"Geometry/residual acceptance: {'PASS' if report['pass'] else 'FAIL'}",
        f"Shell coverage: {len(selected)}/{len(buildings)}. Facades: {len(features)}.",
        "",
        "Walker acceptance is recorded separately by `buildings-validate`.",
        "",
        "| Building | Choice | Median m | p75 m | Triangles | Hausdorff m | Pass |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for r in records:
        v = r["candidates"].get(r["choice"], {})
        lines.append(
            f"| {r['building_id']} | {r['choice'] or 'missing'} | {v.get('median_m', '—')} | "
            f"{v.get('p75_m', '—')} | {v.get('triangles', '—')} | {v.get('hausdorff_m', '—')} | {r['pass']} |"
        )
        for name, metrics in r["candidates"].items():
            if "error" in metrics:
                errors.append(f"- `{r['building_id']}` / {name}: {metrics['error']}")
    (output / "metrics.md").write_text(
        "\n".join(lines + ["\n## Rejected candidates\n"] + errors) + "\n"
    )
    code_files = sorted(Path(__file__).parent.glob("*.py")) + [
        Path(C.__file__),
        C.REPO_ROOT / "pipeline/turku3d/layers.py",
        C.REPO_ROOT / "pipeline/turku3d/package/gltf.py",
        C.REPO_ROOT / "pipeline/turku3d/col/materials_v1.json",
    ]
    code_hash = hashlib.sha256("".join(sha256(p) for p in code_files).encode()).hexdigest()
    write_manifest(
        output,
        inputs=inputs,
        params={
            **params,
            "seams": "objects",
            "git_revision": subprocess.check_output(
                ["git", "-C", str(C.REPO_ROOT), "rev-parse", "HEAD"], text=True
            ).strip(),
            "geometry_acceptance": report["shell_coverage_pass"],
            "accuracy_acceptance": report["pass"],
            "walker_validation": "not_run",
        },
        code=code_hash,
        tiles=files,
    )
    return report


def publish(buildings, selected, records, output, streets, ortho_paths, inputs, params):
    """Prepare the complete layer before replacing any existing artifact."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".buildings-", dir=output.parent) as temporary:
        stage = Path(temporary) / "buildings"
        report = _publish(buildings, selected, records, stage, streets, ortho_paths, inputs, params)
        output.mkdir(parents=True, exist_ok=True)
        previous = output / "MANIFEST.json"
        old = json.loads(previous.read_text()) if previous.exists() else {}
        fresh = {p.name for p in stage.iterdir()}
        for name in [
            *old.get("tiles", {}),
            *old.get("tiles_written", []),
            *old.get("artifacts", []),
            "walkers.json",
        ]:
            if Path(name).name != name:
                raise ValueError("unsafe layer manifest path")
            if name not in fresh:
                (output / name).unlink(missing_ok=True)
        for path in sorted(stage.iterdir(), key=lambda p: p.name == "MANIFEST.json"):
            path.replace(output / path.name)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-area", default="work-3x3")
    ap.add_argument(
        "--roofer-output", type=Path, help="Reuse an existing CityJSONSeq reconstruction"
    )
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args(argv)
    started = time.monotonic()
    cache = C.work_root() / "cache/buildings"
    cache.mkdir(parents=True, exist_ok=True)
    source = C.raw_dir("wfs") / args.source_area
    paths = {
        name: source / (name + ".parquet")
        for name in ["kanta_Rakennus", "bldg_Building_LOD2", "GIS_Keskilinjat"]
    }
    buildings = read_footprints(paths["kanta_Rakennus"])
    print(f"Footprints: {len(buildings)}", flush=True)
    if not buildings:
        raise ValueError("no AOI buildings")
    osm_paths = sorted((C.raw_dir("osm") / args.source_area).glob("*.parquet"))
    if not osm_paths:
        raise FileNotFoundError("OSM GeoParquet is required")
    attach_osm(buildings, osm_paths[-1])
    # Include complete buildings crossing the AOI boundary, not only points inside the AOI.
    bounds = unary_union([b.footprint for b in buildings]).bounds
    bounds = C.BBoxLocal(*bounds).buffered(5)
    laz = [
        C.raw_dir("laz") / "pistepilvi" / (t.name + ".laz") for t in C.laz_tiles_for_bbox(bounds)
    ]
    reconstruction = args.roofer_output or cache / "roofer"
    execution = reconstruction / "execution.json"
    command = json.loads(execution.read_text()).get("command") if execution.exists() else None
    if args.roofer_output is None:
        footprints = cache / "footprints.gpkg"
        write_roofer_footprints(buildings, footprints)
        command = roofer.run(laz, footprints, reconstruction, args.jobs)
    seq = sorted(reconstruction.glob("*.city.jsonl"))
    if not seq:
        raise FileNotFoundError(f"no CityJSONSeq files in {reconstruction}")
    reconstructed = roofer.read(seq)
    reconstruction_metadata = roofer.read_metadata(seq)
    city, matches = citygml.read(paths["bldg_Building_LOD2"], buildings)
    print(f"Candidates: city={len(city)}, roofer={len(reconstructed)}; reading class 6", flush=True)
    point_key = hashlib.sha256(json.dumps([(str(p), sha256(p)) for p in laz]).encode()).hexdigest()
    points_path = cache / (point_key + ".class6.npy")
    # Cache whole LAZ bounds so changes in footprint/AOI cannot truncate reused points.
    if points_path.exists():
        points = np.load(points_path, mmap_mode="r")
    else:
        all_bounds = [C.LazTile.parse(p.stem).bbox_en.to_local() for p in laz]
        bb = C.BBoxLocal(
            min(b.xmin for b in all_bounds),
            min(b.zmin for b in all_bounds),
            max(b.xmax for b in all_bounds),
            max(b.zmax for b in all_bounds),
        )
        points = read_points(laz, bb)
        np.save(points_path, points)
    missing = [
        b for b in buildings if reconstruction_metadata.get(b.id, {}).get("rf_pointcloud_unusable")
    ]
    overlap = overlap_points(laz, missing, cache, point_key) if missing else np.empty((0, 3))
    selected, records = {}, []
    for i, building in enumerate(buildings):
        x0, z0, x1, z1 = building.footprint.bounds
        padding = (
            100
            if reconstruction_metadata.get(building.id, {}).get("rf_pointcloud_unusable")
            else 20
        )
        keep = (
            (points[:, 0] >= x0 - padding)
            & (points[:, 0] <= x1 + padding)
            & (points[:, 2] >= z0 - padding)
            & (points[:, 2] <= z1 + padding)
        )
        shell, record = choose(
            building,
            {"city": city.get(building.id, []), "roofer": reconstructed.get(building.id, [])},
            points[keep],
        )
        if not record["pass"]:
            reason = "no_valid_reconstruction" if shell is None else "residual_limits_exceeded"
            try:
                surfaces, evidence = lidar_extrusion(
                    building, points[keep], reconstruction_metadata.get(building.id, {}), overlap
                )
                alternatives = {
                    "city": city.get(building.id, []),
                    "roofer": reconstructed.get(building.id, []),
                    "lidar_extrusion": surfaces,
                }
                shell, record = choose(building, alternatives, points[keep])
                record["fallback"] = {
                    "reason": reason,
                    "selected": record["choice"] == "lidar_extrusion",
                    "height_evidence": evidence,
                }
            except ValueError as exc:
                record["fallback"] = {"reason": reason, "error": str(exc)}
        record["shell_valid"] = shell is not None
        record["city_match"] = matches.get(building.id)
        record["roofer_metadata"] = reconstruction_metadata.get(building.id)
        if shell is not None:
            selected[building.id] = shell
        records.append(record)
        print(
            f"{i + 1}/{len(buildings)} {building.id}: {record['choice']}, pass={record['pass']}",
            flush=True,
        )
    streets = unary_union(
        [local_polygon(p) for p in gpd.read_parquet(paths["GIS_Keskilinjat"]).geometry]
    )
    ortho = sorted((C.raw_dir("ortho") / "ortho2022to" / args.source_area).glob("*.tif"))
    input_files = [*paths.values(), *laz, osm_paths[-1], *seq, *ortho]
    if execution.exists():
        input_files.append(execution)
    inputs = {str(p): sha256(p) for p in input_files}
    params = {
        "aoi_local": envelope().as_tuple(),
        "median_limit_m": 0.10,
        "p75_limit_m": 0.25,
        "residual_metric": "euclidean_point_to_shell",
        "hausdorff_limit_m": 0.3,
        "triangle_limit": 2000,
        "roofer_command": command,
        "roof_uv_origin_en": [C.ORIGIN_E, C.ORIGIN_N],
    }
    output = C.work_root() / "build/layers/buildings"
    report = publish(buildings, selected, records, output, streets, ortho, inputs, params)
    print(
        json.dumps(
            {
                "pass": report["shell_coverage_pass"],
                "accuracy_pass": report["pass"],
                "shell_coverage_pass": report["shell_coverage_pass"],
                "fallbacks": report["fallback_count"],
                "shells": len(selected),
                "buildings": len(buildings),
                "elapsed_s": round(time.monotonic() - started, 2),
                "output": str(output),
            }
        ),
        flush=True,
    )
    return 0 if report["shell_coverage_pass"] else 1
