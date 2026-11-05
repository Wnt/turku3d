"""AHN point clouds -> the classified block clouds (``raw/laz/pistepilvi/<block>.laz``) the lidar producers read.

Owner boundary: this module (AHN classification and anything derived from the point cloud) vs ``convert.py``
(ground key points, BGT/OSM/3DBAG vector layers)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from turku3d import config as C

from .convert import _files, bgt_features


VERSION = 2  # classification rules below; a point cloud written by an older version is rewritten
HAG_CELL = 1.0  # m: height-above-ground raster from the block's ground key points
VEG_HIGH, VEG_MEDIUM, VEG_LOW = 2.0, 0.5, 0.15  # m above ground: Turku's classes 5 / 4 / 3
STREET_OBJECTS = 2.5  # m: AHN class 1 below this over BGT carriageways is cars and street furniture, not vegetation
FACADE = 1.0  # m around a building (class 6 roof cells, BGT pand): facades, balconies, dormers stay unclassified
ROOF_CLEAR = 0.5  # m above the roof top next to it: class 1 higher than that over a building is an overhanging crown
AFLOAT = 3.0  # m above the water surface: boats, pontoons and moorings, not vegetation
# Wire returns (turku3d.wire_points) stay class 1 as in Turku's survey, where the wire producers look for them.
# AHN files crowns in class 1 too, so the candidates crowd themselves (as overlap returns do in Turku).
WIRE_STAGE = {"min_extent": 4.0, "min_points": 8, "self_crowd": 6}
SOLID_CELL = 1.0  # m: plan cells in which a solid object (monument, kiosk, mast, sculpture) is told from a crown
SOLID_SHARE = 0.2  # a crown splits pulses: below this share of multiple-return candidates a cell is solid
SOLID_MIN = 4  # candidate returns a cell needs before it can be called solid
CIVIL, BRIDGE = 26, 17  # AHN "kunstwerk" (bridges, viaducts, quays) -> ASPRS bridge deck, which bridge profiles prefer


def split_unclassified(x, y, z, cls, hag, building_top, street, water, origin, returns=None):
    """Turku's classes for AHN returns (2 ground, 6 building, 9 water, 26 civil structures -> 17, the rest 1).

    AHN class 1 holds everything else together: crowns, facades and roof furniture, wires, vehicles, boats.
    In order, these stay 1 and the rest is vegetation 3/4/5 by height above ground:
      - next to a building (``building_top``: the local roof top within ``FACADE``, NaN elsewhere) and below
        ``ROOF_CLEAR`` over that roof: facades, balconies, dormers, chimneys;
      - over BGT water below ``AFLOAT``: boats and pontoons;
      - over carriageways below ``STREET_OBJECTS``: vehicles and street furniture;
      - wire returns (line-like stretches in free air, ``WIRE_STAGE``);
      - solid objects: a ``SOLID_CELL`` plan cell whose candidates are almost all single returns (``returns``,
        the pulse's number of returns; vegetation, leafed or bare, splits pulses).
    ``building_top``, ``street``, ``water`` and ``returns`` are per point."""
    from turku3d.wire_points import RECIPE, wire_points

    out = np.ones(len(cls), np.uint8)
    out[cls == 2], out[cls == 6], out[cls == 9] = 2, 6, 9
    out[cls == CIVIL] = BRIDGE
    other = cls == 1
    attached = other & (z <= np.nan_to_num(building_top, nan=-np.inf) + ROOF_CLEAR)
    afloat = other & water & (hag < AFLOAT)
    vehicle = other & street & (hag < STREET_OBJECTS)
    veg = other & ~attached & ~afloat & ~vehicle
    probe = out.copy()  # wire search: vegetation candidates unclassified (1), facade returns as building
    probe[attached] = 6
    wires = np.zeros(len(cls), bool)
    if (veg & (hag > RECIPE["min_hag"])).any():
        found, _ = wire_points(x, y, z, probe, hag, r={**RECIPE, **WIRE_STAGE}, origin=origin, workers=2)
        wires[found] = True
    veg &= ~wires
    if returns is not None and veg.any():
        idx = np.flatnonzero(veg)
        cell = np.floor(np.c_[x[idx] - origin[0], y[idx] - origin[1]] / SOLID_CELL).astype(np.int64)
        _, inverse, counts = np.unique(cell, axis=0, return_inverse=True, return_counts=True)
        inverse = inverse.ravel()
        split = np.bincount(inverse, weights=np.asarray(returns)[idx] > 1, minlength=len(counts))
        solid = (counts >= SOLID_MIN) & (split < SOLID_SHARE * counts)
        veg[idx[solid[inverse]]] = False
    out[veg & (hag >= VEG_LOW)] = 3
    out[veg & (hag >= VEG_MEDIUM)] = 4
    out[veg & (hag >= VEG_HIGH)] = 5
    return out


def building_tops(x, y, z, cls, pand, emin, nmax, nx, ny):
    """Per ``HAG_CELL`` cell: the highest class-6 return within ``FACADE`` (BGT pand cells without roof returns
    count as unbounded), NaN away from buildings."""
    from scipy.ndimage import binary_dilation, maximum_filter

    roof = cls == 6
    col = np.clip(((x[roof] - emin) / HAG_CELL).astype(int), 0, nx - 1)
    row = np.clip(((nmax - y[roof]) / HAG_CELL).astype(int), 0, ny - 1)
    top = np.full((ny, nx), -np.inf)
    np.maximum.at(top, (row, col), z[roof])
    reach = int(np.ceil(FACADE / HAG_CELL))
    near = binary_dilation(np.isfinite(top) | pand, np.ones((2 * reach + 1,) * 2, bool))
    # The roof top next to a facade: the wider window reaches the roof edge from a balcony.
    local = maximum_filter(top, size=4 * reach + 1)
    local[near & ~np.isfinite(local)] = np.inf
    return np.where(near, local, np.nan)


def pointcloud_block(block: C.LazTile, ground_points: np.ndarray, log=print) -> Path | None:
    """``raw/laz/pistepilvi/<block>.laz`` from AHN in Turku's classification (2 ground, 3/4/5 low/medium/high
    vegetation, 6 building, 9 water, 17 bridge, 1 other), for roof refinement, LiDAR trees, canopy, walls and wires.

    AHN leaves vegetation, facades, wires, street furniture, vehicles and boats together in class 1:
    ``split_unclassified`` keeps the non-vegetation in 1 and splits the rest by height above the block's ground
    (``HAG_CELL`` raster from the key points). AHN class 26 (civil structures: bridges, quays) becomes 17."""
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
    water_parts = [g for _, _, _, g in bgt_features(("waterdeel",), bbox, log)]
    water = shapely.contains_xy(shapely.union_all(water_parts), gx, gy) if water_parts else np.zeros_like(hole)
    pand_parts = [g for _, _, _, g in bgt_features(("pand",), bbox, log)]
    pand = shapely.contains_xy(shapely.union_all(pand_parts), gx, gy) if pand_parts else np.zeros_like(hole)
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
    tops = building_tops(pts["x"], pts["y"], pts["z"], cls, pand, b.emin, b.nmax, nx, ny)
    out = split_unclassified(pts["x"], pts["y"], pts["z"], cls, hag, tops[row, col], street[row, col],
                             water[row, col], origin=(b.emin, b.nmin), returns=pts["number_of_returns"])
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
    Registry(C.raw_dir("laz")).record(dest, url=None, derived_from=[p.name for p in sources], classes=counts,
                                      classification_version=VERSION)
    log(f"nl   point cloud {block.name}: {len(out)} points, classes {counts}")
    return dest


def pointclouds(log=print) -> int:
    """Write the classified point cloud of every block that has ground key points but no point cloud of this
    ``VERSION`` yet."""
    import laspy

    from turku3d.fetch.common import Registry

    registry = Registry(C.raw_dir("laz"))
    count = 0
    for path in sorted((C.raw_dir("laz") / "maanpinta").glob("*.laz")):
        cloud = C.raw_dir("laz") / "pistepilvi" / path.name
        if cloud.exists() and (registry.get(cloud) or {}).get("classification_version") == VERSION:
            continue
        with laspy.open(path) as reader:
            las = reader.read()
        pointcloud_block(C.LazTile.parse(path.stem), np.column_stack([las.x, las.y, las.z]), log)
        count += 1
    return count


