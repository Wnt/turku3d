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
from .inputs import (
    attach_osm,
    local_polygon,
    read_footprints,
    read_playable_footprints,
)
from .reconstruct import evidence_lidar, reconstruct, roofer_lidar, sha256
from .tiles import collision_tiles, ownership_point


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _publish(
    buildings,
    selected,
    records,
    output,
    streets,
    ortho_paths,
    inputs,
    params,
    visual_tiles=None,
    store=None,
):
    """Failed quality remains explicit; products contain only valid closed shells."""
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (store or {}).items():
        (output / name).write_bytes(data)
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
            building,
            shell,
            streets,
            [b.footprint for b in buildings],
            key,
            placeholder=record["choice"] == "placeholder",
        )
        # Whole objects are owned once by the tile containing their representative point.
        # Assembler uses actual mesh bounds for cross-tile culling.
        point = ownership_point(building.footprint, visual_tiles)
        tile = C.VisualTile.at(point.x, point.y)
        visuals[tile.id].extend(meshes)
        features.extend(facades)
        accepted.append(shell)
    owners = [b for b in buildings if b.id in selected]
    for tid, arrays in collision_tiles(
        accepted,
        [b.footprint for b in owners],
        ids=[b.id for b in owners],
        visual_tiles=visual_tiles,
    ).items():
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
        "model_count": sum(r["choice"] in ("city", "roofer") for r in records),
        "lidar_shell_count": sum(r["choice"] in ("lidar_extrusion", "lidar_flat") for r in records),
        "placeholder_count": sum(r["choice"] == "placeholder" for r in records),
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


def publish(
    buildings,
    selected,
    records,
    output,
    streets,
    ortho_paths,
    inputs,
    params,
    visual_tiles=None,
    store=None,
):
    """Prepare the complete layer before replacing any existing artifact.

    `store` holds the per-building reconstruction files the next build reuses."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".buildings-", dir=output.parent) as temporary:
        stage = Path(temporary) / "buildings"
        report = _publish(
            buildings,
            selected,
            records,
            stage,
            streets,
            ortho_paths,
            inputs,
            params,
            visual_tiles,
            store,
        )
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
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument(
        "--tiles",
        nargs="+",
        default=C.playable_tile_ids(),
        help="L0 visual tiles to cover (default: the four playable tiles)",
    )
    args = ap.parse_args(argv)
    started = time.monotonic()
    cache = C.data_root() / "cache/buildings"
    cache.mkdir(parents=True, exist_ok=True)
    source = C.raw_dir("wfs") / args.source_area
    paths = {
        name: source / (name + ".parquet")
        for name in ["kanta_Rakennus", "GIS_Rakennukset", "bldg_Building_LOD2", "GIS_Keskilinjat"]
    }
    visual_tiles = [C.VisualTile.parse(tid) for tid in args.tiles]
    if any(tile.level != 0 for tile in visual_tiles):
        raise ValueError("building source tiles must be L0")
    region = C.BBoxLocal(
        min(t.bbox.xmin for t in visual_tiles),
        min(t.bbox.zmin for t in visual_tiles),
        max(t.bbox.xmax for t in visual_tiles),
        max(t.bbox.zmax for t in visual_tiles),
    )
    buildings = read_playable_footprints(paths["kanta_Rakennus"], paths["GIS_Rakennukset"], region)
    from shapely.geometry import box

    tile_regions = [box(t.bbox.xmin, t.bbox.zmin, t.bbox.xmax, t.bbox.zmax) for t in visual_tiles]
    buildings = [b for b in buildings if any(b.footprint.intersects(r) for r in tile_regions)]
    print(f"Footprints: {len(buildings)}", flush=True)
    if not buildings:
        raise ValueError("no AOI buildings")
    osm_paths = sorted((C.raw_dir("osm") / args.source_area).glob("*.parquet"))
    if not osm_paths:
        raise FileNotFoundError("OSM GeoParquet is required")
    attach_osm(buildings, osm_paths[-1])
    established = {
        b.id
        for b in read_footprints(paths["kanta_Rakennus"], region)
        if any(b.footprint.intersects(r) for r in tile_regions)
    }
    city, matches = citygml.read(paths["bldg_Building_LOD2"], buildings)
    output = C.work_root() / "build/layers/buildings"
    selected, records, store, counts = reconstruct(
        buildings, established, city, matches, output, cache, args.jobs
    )
    print(f"Reconstruction: {json.dumps(counts)}", flush=True)
    streets = unary_union(
        [local_polygon(p) for p in gpd.read_parquet(paths["GIS_Keskilinjat"]).geometry]
    )
    ortho = sorted((C.raw_dir("ortho") / "ortho2022to" / args.source_area).glob("*.tif"))
    # Only the LAZ tiles under or near some building: an L-shaped tile list's bounding box can
    # reach lidar tiles nobody fetched because nothing there is built.
    laz = sorted({p for b in buildings for p in (*roofer_lidar(b), *evidence_lidar(b))})
    inputs = {str(p): sha256(p) for p in [*paths.values(), *laz, osm_paths[-1], *ortho]}
    params = {
        "aoi_local": region.as_tuple(),
        "visual_tiles": args.tiles,
        "median_limit_m": 0.10,
        "p75_limit_m": 0.25,
        "residual_metric": "euclidean_point_to_shell",
        "hausdorff_limit_m": 0.3,
        "triangle_limit": 2000,
        "roofer": {"version": roofer.version(), "flags": roofer.FLAGS},
        "roof_uv_origin_en": [C.ORIGIN_E, C.ORIGIN_N],
    }
    report = publish(
        buildings, selected, records, output, streets, ortho, inputs, params, visual_tiles, store
    )
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
