"""Reproducible AOI run, vector matching and honest length-weighted coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import numpy as np
from shapely import force_2d, union_all
from shapely.geometry import LineString, box

from turku3d import config as C

from .cache import PointCloud, TileStore
from .edges import edge_lines, measured_breaklines
from .objects import small_objects
from .profiles import stations
from .surface import Surface, select_ground, split_points, surfaces


def aoi_bbox(scope):
    if scope == "office-block":
        return C.OFFICE_BLOCK.with_margin
    tiles = C.OFFICE_BLOCK.visual_tiles
    return C.BBoxLocal(
        min(t.bbox.xmin for t in tiles),
        min(t.bbox.zmin for t in tiles),
        max(t.bbox.xmax for t in tiles),
        max(t.bbox.zmax for t in tiles),
    ).buffered(C.MESH_MARGIN)


def line_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry] if geometry.length > 0 else []
    return [part for geom in getattr(geometry, "geoms", []) for part in line_parts(geom)]


def vector_curbs(bbox):
    path = C.raw_dir("wfs") / "work-3x3/kanta_Liikennevayla.parquet"
    frame = gpd.read_parquet(path)
    clip = box(*bbox.as_tuple())
    result = []
    for _, row in frame[frame.kplkoodi.astype(str) == "62520"].iterrows():
        geom = row.geometry_local
        # WFS geometry_local stores x/z for 2D and x/z/H for 3D (see to_local).
        geom = force_2d(geom).intersection(clip)
        for i, line in enumerate(line_parts(geom)):
            result.append((f"vector:{row.feature_key}:{i}", line))
    return result


def code_hash():
    return hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))
    ).hexdigest()


def excluded_surfaces(bbox):
    """Building interiors and water are not street relief."""
    root = C.raw_dir("wfs") / "work-3x3"
    buildings = gpd.read_parquet(root / "GIS_Rakennukset.parquet")
    building_mask = union_all(
        [
            force_2d(g)
            for g in buildings.geometry_local
            if g.geom_type in ("Polygon", "MultiPolygon")
        ]
    ).buffer(0.3)
    osm_path = sorted((C.raw_dir("osm") / "work-3x3").glob("*.parquet"))[-1]
    osm = gpd.read_parquet(osm_path)
    water = union_all([force_2d(g) for g in osm[osm.natural == "water"].geometry_local])
    return union_all([building_mask, water]).intersection(box(*bbox.as_tuple()))


def run(scope="phase1", resolution=0.25, output=None, log=print):
    started = perf_counter()
    bbox = aoi_bbox(scope)
    output = Path(output or C.work_root() / "reports" / "lidar" / scope)
    output.mkdir(parents=True, exist_ok=True)
    store = TileStore()
    log(f"Loading {scope}: {bbox.as_tuple()}", flush=True)
    cloud = store.query(bbox.buffered(2))
    log(f"Loaded {len(cloud):,} points in {perf_counter() - started:.1f}s", flush=True)
    sources, source_key = store._key(bbox.buffered(2))
    selection_code = Path(__file__).with_name("surface.py").read_bytes()
    # Split parameters are part of the cache contract, not just a source-code hash.
    key = hashlib.sha256(
        source_key.encode() + selection_code + b"coordinate-hash-training-80-v1"
    ).hexdigest()
    ground_path = store.cache_dir / f"ground-{key}.npy"
    if ground_path.exists():
        points = np.load(ground_path, mmap_mode="r", allow_pickle=False)
    else:
        training, _ = split_points(cloud.points, cloud.records["classification"])
        points, selection = select_ground(PointCloud.from_xyz(*training))
        np.save(ground_path, points, allow_pickle=False)
        log(f"Ground selection: {selection}", flush=True)
    surface = Surface(points)
    log(f"Ground ready: {len(points):,} points; profiling vectors", flush=True)
    vectors = vector_curbs(bbox)
    vector_features = []
    total, supported, raised, flush = 0.0, 0.0, 0.0, 0.0
    station_steps, unsupported = [], []
    for ident, line in vectors:
        features, measured = measured_breaklines(surface, line, ident, "kantakartta+lidar")
        vector_features.extend(features)
        d, centers, _ = stations(line)
        total += line.length
        for i, length in enumerate(np.diff(d)):
            p, q = measured[i : i + 2]
            if p is None or q is None:
                unsupported.append(centers[i : i + 2].tolist())
                continue
            supported += float(length)
            height = 0.5 * (abs(p.step) + abs(q.step))
            station_steps.append((height, float(length)))
            if height >= 0.03:
                raised += float(length)
            else:
                flush += float(length)
    log(
        f"Vector support {supported:.1f}/{total:.1f}m ({supported / max(total, 1):.1%}); rasterizing",
        flush=True,
    )
    # Ground selection is shared with profiling; avoid fitting the same millions of points twice.
    raster = surfaces(cloud, bbox, resolution, ground_points=points)
    raster.save(output / "surfaces.npz")
    candidates = edge_lines(raster)
    log(f"Raster candidates: {len(candidates)} polylines", flush=True)
    vector_corridor = union_all(
        [line.buffer(0.8) for _, line in vectors] + [excluded_surfaces(bbox)]
    )
    added = []
    for i, candidate in enumerate(candidates):
        for j, line in enumerate(line_parts(candidate.difference(vector_corridor))):
            if line.length < 1.5:
                continue
            features, _ = measured_breaklines(
                surface, line, f"lidar:{i}:{j}", "lidar", require_step=True
            )
            added.extend(f for f in features if LineString(f["centers_xz"]).length >= 2.0)
    objects = small_objects(raster)
    features = vector_features + added
    (output / "breaklines.json").write_text(json.dumps(features, indent=1, allow_nan=False) + "\n")
    (output / "added-breaklines.json").write_text(
        json.dumps(added, indent=1, allow_nan=False) + "\n"
    )
    (output / "objects.json").write_text(
        json.dumps([o.as_dict() for o in objects], indent=1, allow_nan=False) + "\n"
    )
    by_class = {}
    for feature in added:
        kind = feature["relief_class"]
        by_class[kind] = by_class.get(kind, 0.0) + LineString(feature["centers_xz"]).length
    heights = np.array([v[0] for v in station_steps])
    weights = np.array([v[1] for v in station_steps])

    def percentile(fraction):
        if not len(heights):
            return None
        order = np.argsort(heights)
        return float(
            heights[order][np.searchsorted(np.cumsum(weights[order]), fraction * weights.sum())]
        )

    report = {
        "scope": scope,
        "bbox_local": bbox.as_tuple(),
        "resolution_m": resolution,
        "code_hash": code_hash(),
        "source_key": source_key,
        "sources": [str(store._source(t)[0]) for t in sources],
        "points": len(cloud),
        "ground_points": len(points),
        "vector_length_m": total,
        "supported_length_m": supported,
        "confirmed_fraction": supported / total if total else 0,
        "raised_length_m": raised,
        "flush_length_m": flush,
        "unsupported_length_m": total - supported,
        "median_step_m": percentile(0.5),
        "p90_step_m": percentile(0.9),
        "added_lidar_length_m": by_class,
        "added_polylines": len(added),
        "vector_polylines": len(vector_features),
        "small_object_candidates": len(objects),
        "elapsed_s": perf_counter() - started,
        "fitting_split": "80% deterministic coordinate hash; 20% withheld before ground selection",
        "confirmation_definition": "Adjacent supported slope-fitted stations, including measured flush/drop spans. Not proof of a raised curb.",
        "mesh_coverage": None,
    }
    from .report import write_report

    write_report(output, report, vectors, vector_features, added, unsupported, bbox)
    log(json.dumps(report, indent=2), flush=True)
    return report
