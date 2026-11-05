"""AHN point clouds -> the classified block clouds (``raw/laz/pistepilvi/<block>.laz``) the lidar producers read.

Owner boundary: this module (AHN classification and anything derived from the point cloud) vs ``convert.py``
(ground key points, BGT/OSM/3DBAG vector layers)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from turku3d import config as C

from .convert import _files, bgt_features


HAG_CELL = 1.0  # m: height-above-ground raster from the block's ground key points
VEG_HIGH, VEG_MEDIUM, VEG_LOW = 2.0, 0.5, 0.15  # m above ground: Turku's classes 5 / 4 / 3
STREET_OBJECTS = 2.5  # m: AHN class 1 below this over BGT carriageways is cars and street furniture, not vegetation


def pointcloud_block(block: C.LazTile, ground_points: np.ndarray, log=print) -> Path | None:
    """``raw/laz/pistepilvi/<block>.laz`` from AHN in Turku's classification (2 ground, 3/4/5 low/medium/high
    vegetation, 6 building, 9 water, 1 other), for roof refinement, LiDAR trees, canopy, walls and wires.

    AHN leaves vegetation, street furniture and vehicles together in class 1: they are split by height above
    the block's ground (``HAG_CELL`` raster from the key points), and class 1 below ``STREET_OBJECTS`` over
    BGT carriageways stays class 1. AHN class 26 (civil structures: bridges, quays) stays 1."""
    import laspy
    import shapely
    from scipy.interpolate import griddata

    from turku3d.fetch.common import Registry

    b = block.bbox_en
    bbox = (b.emin, b.nmin, b.emax, b.nmax)
    sources = _files("ahn", bbox)
    if not sources or len(ground_points) < 16:
        return None
    nx = ny = int(round((b.emax - b.emin) / HAG_CELL))
    gx, gy = np.meshgrid(b.emin + (np.arange(nx) + 0.5) * HAG_CELL, b.nmax - (np.arange(ny) + 0.5) * HAG_CELL)
    ground = griddata(ground_points[:, :2], ground_points[:, 2], (gx, gy), method="linear")
    hole = ~np.isfinite(ground)
    if hole.any():
        ground[hole] = griddata(ground_points[:, :2], ground_points[:, 2], (gx[hole], gy[hole]), method="nearest")
    roads = [g for c, _, p, g in bgt_features(("wegdeel",), bbox, log) if (p.get("functie") or "").startswith(("rijbaan", "parkeervlak", "OV-baan", "inrit"))]
    street = shapely.contains_xy(shapely.union_all(roads), gx, gy) if roads else np.zeros_like(hole)
    keep = {k: [] for k in ("x", "y", "z", "intensity", "return_number", "number_of_returns", "classification",
                            "gps_time", "red", "green", "blue")}
    for path in sources:
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(4_000_000):
                x, y = np.asarray(chunk.x), np.asarray(chunk.y)
                inside = (x >= b.emin) & (x < b.emax) & (y > b.nmin) & (y <= b.nmax)
                if not inside.any():
                    continue
                for k in keep:
                    keep[k].append(np.asarray(getattr(chunk, k))[inside])
    if not keep["x"]:
        return None
    pts = {k: np.concatenate(v) for k, v in keep.items()}
    cls = pts["classification"].astype(np.uint8)
    col = np.clip(((pts["x"] - b.emin) / HAG_CELL).astype(int), 0, nx - 1)
    row = np.clip(((b.nmax - pts["y"]) / HAG_CELL).astype(int), 0, ny - 1)
    hag = pts["z"] - ground[row, col]
    out = np.ones(len(cls), np.uint8)
    out[cls == 2], out[cls == 6], out[cls == 9] = 2, 6, 9
    other = cls == 1
    vehicle = other & street[row, col] & (hag < STREET_OBJECTS)
    veg = other & ~vehicle
    out[veg & (hag >= VEG_LOW)] = 3
    out[veg & (hag >= VEG_MEDIUM)] = 4
    out[veg & (hag >= VEG_HIGH)] = 5
    root = C.raw_dir("laz") / "pistepilvi"
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{block.name}.laz"
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.array([np.floor(b.emin), np.floor(b.nmin), 0.0])
    las = laspy.LasData(header)
    las.x, las.y, las.z = pts["x"], pts["y"], pts["z"]
    for k in ("intensity", "gps_time", "red", "green", "blue"):
        setattr(las, k, pts[k])
    # LAS 1.4 format 8 counts up to 15 returns; format 3 (Turku's) up to 7.
    las.return_number = np.clip(pts["return_number"], 0, 7)
    las.number_of_returns = np.clip(pts["number_of_returns"], 0, 7)
    las.classification = out
    tmp = dest.with_name(dest.name + ".tmp.laz")
    las.write(tmp)
    tmp.replace(dest)
    counts = {int(k): int(v) for k, v in zip(*np.unique(out, return_counts=True))}
    Registry(C.raw_dir("laz")).record(dest, url=None, derived_from=[p.name for p in sources], classes=counts)
    log(f"nl   point cloud {block.name}: {len(out)} points, classes {counts}")
    return dest


def pointclouds(log=print) -> int:
    """Write the classified point cloud of every block that has ground key points but no point cloud yet."""
    import laspy

    count = 0
    for path in sorted((C.raw_dir("laz") / "maanpinta").glob("*.laz")):
        if (C.raw_dir("laz") / "pistepilvi" / path.name).exists():
            continue
        with laspy.open(path) as reader:
            las = reader.read()
        pointcloud_block(C.LazTile.parse(path.stem), np.column_stack([las.x, las.y, las.z]), log)
        count += 1
    return count


