"""Lidar-height massing for buildings outside the four playable L0 tiles."""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import laspy
import numpy as np
import shapely
from scipy.spatial import cKDTree

from turku3d import config as C
from turku3d.layers import write_manifest, write_visual
from turku3d.package.gltf import GlbMesh

from .fallback import extrusion
from .inputs import local_polygon
from .shells import make_shell
from .tiles import ownership_point


def lidar_points(paths):
    """Read only ground and building returns into bounded local arrays."""
    ground, roofs = [], []
    for path in paths:
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(1_000_000):
                cls = np.asarray(chunk.classification)
                for kind, target in ((2, ground), (6, roofs)):
                    mask = cls == kind
                    if mask.any():
                        x, y, z = C.en_to_local(
                            np.asarray(chunk.x)[mask],
                            np.asarray(chunk.y)[mask],
                            np.asarray(chunk.z)[mask],
                        )
                        target.append(np.column_stack((x, y, z)).astype(np.float32))
    return np.concatenate(ground), np.concatenate(roofs)


def height_for(footprint, ground, ground_tree, roofs, roof_tree):
    center = footprint.representative_point()
    _, ground_index = ground_tree.query([center.x, center.y])
    base = float(ground[ground_index, 1])
    xmin, zmin, xmax, zmax = footprint.bounds
    radius = max(np.hypot(xmax - xmin, zmax - zmin), 12)
    indices = roof_tree.query_ball_point([center.x, center.y], radius)
    candidates = roofs[indices]
    inside = shapely.intersects_xy(footprint, candidates[:, 0], candidates[:, 2])
    elevations = candidates[inside, 1]
    if len(elevations) < 3:
        distance, nearby = roof_tree.query([center.x, center.y], k=3)
        if np.max(distance) > 25:
            return base, None
        elevations = roofs[nearby, 1]
    height = float(np.median(elevations))
    return base, height if height > base + 2 else None


def main():
    started = time.monotonic()
    core = C.PLAYABLE_TILES.core
    core_polygon = shapely.box(core.xmin, core.zmin, core.xmax, core.zmax)
    source = C.raw_dir("wfs") / "work-3x3/GIS_Rakennukset.parquet"
    area = C.WORK_3X3
    frame = gpd.read_parquet(source)
    buildings = []
    for row in frame.itertuples():
        if row.geometry.geom_type not in ("Polygon", "Point"):
            continue
        source_geometry = shapely.force_2d(row.geometry)
        if source_geometry.geom_type == "Point":
            source_geometry = source_geometry.buffer(3, cap_style="square")
        polygon = local_polygon(source_geometry)
        if (
            not polygon.is_valid
            or not polygon.intersects(shapely.box(*area.core.as_tuple()))
            or polygon.intersects(core_polygon)
        ):
            continue
        buildings.append((row.feature_key, polygon, row.Kerrosluku))
    laz = [C.raw_dir("laz") / "pistepilvi" / f"{t.name}.laz" for t in area.laz_tiles]
    missing = [str(p) for p in laz if not p.exists()]
    if missing:
        raise FileNotFoundError(f"outer massing needs lidar: {missing}")
    ground, roofs = lidar_points(laz)
    ground_tree = cKDTree(ground[:, [0, 2]])
    roof_tree = cKDTree(roofs[:, [0, 2]])
    output = C.work_root() / "build/layers/buildings_outer"
    output.mkdir(parents=True, exist_ok=True)
    meshes, records = defaultdict(list), []
    tiles = C.visual_tiles_for_bbox(area.core)
    for feature_key, footprint, levels in buildings:
        base, roof = height_for(footprint, ground, ground_tree, roofs, roof_tree)
        state = "lidar_extrusion"
        if roof is None:
            try:
                floors = max(1, min(12, int(float(levels))))
            except (TypeError, ValueError):
                floors = 3
            roof = base + 3 * floors
            state = "placeholder"
        try:
            shell = make_shell(extrusion(footprint, base, roof), footprint)
        except ValueError as exc:
            records.append(
                {"source_id": feature_key, "state": "invalid_outline", "reason": str(exc)}
            )
            continue
        point = ownership_point(footprint, tiles)
        tile = C.VisualTile.at(point.x, point.y)
        ident = "outer-gis-" + feature_key
        meshes[tile.id].append(
            GlbMesh(
                ident,
                shell.positions,
                shell.indices,
                uvs=shell.positions[:, [0, 2]],
                color=(0.86, 0.55, 0.23, 1.0) if state == "placeholder" else (0.8, 0.8, 0.8, 1.0),
                extras={
                    "layer": "buildings_outer",
                    "object_id": ident,
                    "building_id": ident,
                    "material_key": "facade_plaster",
                    "lidar_massing": True,
                },
            )
        )
        records.append({"source_id": feature_key, "state": state, "tile": tile.id})
    files = []
    for tid, tile_meshes in sorted(meshes.items()):
        filename = tid + ".vis.glb"
        write_visual(
            output / filename, tile_meshes, root_extras={"layer": "buildings_outer", "tile": tid}
        )
        files.append(filename)
    result = {
        "source_count": len(buildings),
        "lidar_extruded": sum(r["state"] == "lidar_extrusion" for r in records),
        "placeholders": sum(r["state"] == "placeholder" for r in records),
        "by_tile": {tid: len(value) for tid, value in sorted(meshes.items())},
        "records": records,
        "elapsed_s": round(time.monotonic() - started, 2),
    }
    (output / "metrics.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    def digest(path):
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()

    inputs = {str(p): digest(p) for p in [source, *laz]}
    write_manifest(
        output,
        inputs=inputs,
        params={"area": area.name, "core_excluded": core.as_tuple(), "visual_only": True},
        code=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        tiles=files,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "records"}))
    return 0 if result["lidar_extruded"] + result["placeholders"] == result["source_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
