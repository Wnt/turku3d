"""Read-only city/OSM/ortho adapters; all generated products stay under work_root()."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import shapely
from shapely.geometry import box

from turku3d import config as C

from .mesh import parts

WFS_NAMES = (
    "kanta_Liikennevayla",
    "kanta_Kevyenliikenteenvayla",
    "GIS_Katualueet",
    "GIS_Rakennukset",
    "kanta_Vesikohde",
    "kanta_Rakenne",
)


def source_paths(bounds, source_area="work-3x3"):
    paths = {}
    for tile in C.laz_tiles_for_bbox(bounds):
        options = [
            C.raw_dir("laz-3x3") / f"{tile.name}.laz",
            C.raw_dir("laz") / "pistepilvi" / f"{tile.name}.laz",
        ]
        paths[tile.name] = next((p for p in options if p.exists()), options[0])
    for name in WFS_NAMES:
        paths[name] = C.raw_dir("wfs") / source_area / f"{name}.parquet"
    osm = sorted((C.raw_dir("osm") / source_area).glob("*.parquet"))
    if not osm:
        raise FileNotFoundError(f"OSM GeoParquet missing for {source_area}")
    paths["osm"] = osm[-1]
    paths["ortho"] = C.raw_dir("ortho") / "ortho2022to" / f"{source_area}.vrt"
    for path in paths.values():
        if not path.exists():
            raise FileNotFoundError(path)
    return paths


def fingerprints(paths):
    """Memoized SHA-256s keyed by file identity; raw files are immutable by contract."""
    cache = C.cache_dir("ground") / "input-hashes.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(cache.read_text()) if cache.exists() else {}
    result, seen = {}, {}
    for key, path in paths.items():
        stat = path.stat()
        identity = {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        old = previous.get(key, {})
        if all(old.get(k) == v for k, v in identity.items()):
            identity["sha256"] = old["sha256"]
        elif path in seen:
            identity["sha256"] = seen[path]
        else:
            with path.open("rb") as stream:
                identity["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        seen[path] = identity["sha256"]
        result[key] = identity
    if result != previous:
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n")
        temporary.replace(cache)
    return result


def read_lidar(paths, bounds):
    import laspy

    chunks, classes, water_heights = [], [], []
    for path in paths.values():
        if path.suffix != ".laz":
            continue
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(1_000_000):
                x = np.asarray(chunk.x) - C.ORIGIN_E
                z = C.ORIGIN_N - np.asarray(chunk.y)
                cls = np.asarray(chunk.classification)
                inside = (
                    (x >= bounds.xmin)
                    & (x <= bounds.xmax)
                    & (z >= bounds.zmin)
                    & (z <= bounds.zmax)
                )
                select = inside & np.isin(cls, [2, 12])
                chunks.append(np.c_[x[select], np.asarray(chunk.z)[select], z[select]])
                classes.append(cls[select])
                wet = inside & (cls == 9)
                water_heights.extend(np.asarray(chunk.z)[wet].tolist())
    if not chunks or not any(len(c) for c in chunks):
        raise ValueError("No class-2/12 lidar in the build envelope")
    return np.concatenate(chunks), np.concatenate(classes), np.array(water_heights)


def vectors(paths, bounds):
    import geopandas as gpd

    envelope = box(*bounds.as_tuple())
    frames = {name: gpd.read_parquet(paths[name]) for name in WFS_NAMES}
    lines = []
    for name in WFS_NAMES[:2]:
        frame = frames[name]
        for _, row in frame[frame.kplkoodi.isin(["62520", "62522", "62523"])].iterrows():
            clipped = shapely.force_2d(row.geometry_local).intersection(envelope)
            for i, line in enumerate(parts(clipped)):
                lines.append((f"{row.feature_key}:{i}", line))
    # Dedupe exact city features before fitting (source tables can overlap).
    unique = {}
    for key, line in lines:
        unique.setdefault(shapely.normalize(line).wkb, (key, line))
    osm = gpd.read_parquet(paths["osm"])
    water = []
    for _, row in osm[osm.natural == "water"].iterrows():
        water.extend(parts(shapely.make_valid(shapely.force_2d(row.geometry_local)), ("Polygon",)))
    if not water:
        raise ValueError("No OSM water polygons: refusing to bridge the Aura with ground")
    water = shapely.union_all(water).intersection(envelope)
    buildings = shapely.union_all(
        [
            shapely.make_valid(shapely.force_2d(p))
            for p in frames["GIS_Rakennukset"].geometry_local
            if p.geom_type in ("Polygon", "MultiPolygon")
        ]
    ).intersection(envelope)
    semantic = osm[(osm.highway == "crossing") | osm.kerb.isin(["lowered", "flush"])]
    crossings = shapely.union_all([shapely.force_2d(g) for g in semantic.geometry_local]).buffer(
        1.5
    )
    ramp_lines = []
    for _, row in frames["kanta_Rakenne"][frames["kanta_Rakenne"].kplkoodi == "62540"].iterrows():
        clipped = shapely.force_2d(row.geometry_local).intersection(envelope)
        ramp_lines.extend(
            (f"city:{row.feature_key}:{i}", line) for i, line in enumerate(parts(clipped))
        )
    return list(unique.values()), water, buildings, crossings, frames["GIS_Katualueet"], ramp_lines
