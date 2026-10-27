"""The structures layer, built per listed L0 tile (turku3d.tile_layer).

A tile reads the city survey (kanta_Rakenne: stair nosings, wall tops, fences, ramp lines) and
the OSM steps and barriers of its own input area, clipped to the tile; the city survey wins
where both map the same line. Every object is checked against lidar and the published ground,
then probed with the real-data Rapier character; an object that fails is omitted from the
layer and listed in the tile's metrics, so a new area publishes every structure that passes."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from shapely.geometry import box

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import layer_root
from turku3d.package.manifest import _atomic_write

from .acceptance import validate
from .build import build
from .inputs import Ground, lidar_paths, load_lidar, read_surveys, survey_sources
from .layer import code_hash, omit_failed, write_tile
from .snapshot import snapshot_ground

# Landings, wall sides and probe routes are sampled up to a few metres past a survey line.
REACH = 5.0


def tile_key(tile):
    area = C.source_area(tile)
    reach = tile.bbox.buffered(REACH)
    survey, osm = survey_sources(area)
    paths = {"survey": survey, "osm": osm}
    paths.update({f"laz:{p.stem}": p for p in lidar_paths(reach)})
    ground = layer_root() / "ground"
    window = box(*reach.as_tuple())
    cells = {
        name: entry["sha256"]
        for name, entry in json.loads((ground / "MANIFEST.json").read_text())["tiles"].items()
        if name.endswith(".col.npz")
        and window.intersects(box(*C.CollisionTile.parse(name.split(".")[0]).bbox.as_tuple()))
    }
    paths.update({name: ground / name for name in ("quays.json", "ramps.json")})
    return {"sources": tile_layer.identities(paths), "ground": cells}


def build_tile(tile, staging):
    area = C.source_area(tile)
    records = read_surveys(tile.bbox, area)
    snapshot, _ = snapshot_ground(layer_root() / "ground")
    ground = Ground(snapshot)
    reach = tile.bbox.buffered(REACH)
    meshes, metrics = build(records, load_lidar(lidar_paths(reach), reach), ground, tile.bbox)
    issues = list(metrics["issues"])
    physics = {}
    if meshes:
        report = C.work_root() / "reports/structures" / tile.id
        report.mkdir(parents=True, exist_ok=True)
        result, physics_issues = validate(meshes, ground, metrics["measurements"], report)
        physics = {k: v for k, v in result.items() if k != "results"}
        issues += physics_issues
    shipped, omitted = omit_failed(meshes, issues)
    stats = write_tile(shipped, staging, tile)
    kept = {m.object_id for m in shipped}
    measurements = [
        m
        for m in metrics["measurements"]
        if any(k == m["object_id"] or k.startswith(m["object_id"] + ":") for k in kept)
    ]
    return {
        "records": measurements,
        "metrics": {
            "input_counts": metrics["input_counts"],
            "object_counts": dict(Counter(m.get("kind", "unknown") for m in measurements)),
            "omitted_meshes": omitted,
            "issues": issues,
            "survey_only": metrics["survey_only"],
            "excluded_sources": metrics["excluded_sources"],
            "physics": physics,
            **stats,
        },
    }


def combine(directory, tiles):
    metrics = {
        "tiles": {tid: e["metrics"] for tid, e in tiles.items()},
        "objects": sum(len(e["records"]) for e in tiles.values()),
        "object_counts": dict(
            sum((Counter(e["metrics"]["object_counts"]) for e in tiles.values()), Counter())
        ),
        "omitted_meshes": sum(e["metrics"]["omitted_meshes"] for e in tiles.values()),
        "issues": [i for e in tiles.values() for i in e["metrics"]["issues"]],
        "acceptance": "Every shipped object passed its lidar/ground checks and Rapier probes; "
        "failed objects are omitted and listed under issues.",
    }
    text = json.dumps(metrics, indent=2, allow_nan=False) + "\n"
    _atomic_write(Path(directory) / "metrics.json", text.encode())
    _atomic_write(
        Path(directory) / "report.md",
        f"# Structures layer report\n\n```json\n{text}```\n".encode(),
    )
    return {
        "acceptance_passed": True,
        "objects": metrics["objects"],
        "omitted_meshes": metrics["omitted_meshes"],
    }


PRODUCER = tile_layer.Producer("structures", code_hash, tile_key, build_tile, combine)
