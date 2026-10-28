"""Draft roof envelopes: keep LOD2 terraces/slopes, refine supported planes with class 6."""

from functools import lru_cache

import numpy as np
import shapely

from turku3d import config as C


def lidar_paths(tile):
    """Optional classified sources already on disk; drafts never fetch these large files."""
    return [
        p
        for t in C.laz_tiles_for_bbox(tile.bbox.buffered(100))
        if (p := C.raw_dir("laz") / "pistepilvi" / f"{t.name}.laz").is_file()
    ]


def plane_height(plane, xz):
    return np.asarray(xz) @ np.asarray(plane[:2]) + plane[2]


def polygons(geometry):
    return [
        p
        for p in shapely.get_parts(shapely.make_valid(geometry))
        if p.geom_type == "Polygon" and p.area > 0.01
    ]


def envelope(footprint, surfaces, fallback):
    """Topmost LOD2 plane wins over overlapping lower decks, never their arithmetic mean.

    CityGML includes stacked decks and courtyard extensions. Partition projected polygons
    from highest to lowest; coincident sloping faces share their surveyed ridge edges.
    """
    candidates = []
    for surface in surfaces:
        xyz = np.asarray(surface.exterior.coords)[:-1]
        xz, h = xyz[:, :2], xyz[:, 2]
        center = xz.mean(axis=0)
        design = np.c_[xz - center, np.ones(len(xz))]
        coeff, _, rank, _ = np.linalg.lstsq(design, h, rcond=None)
        if rank < 3:
            continue
        if np.max(np.abs(design @ coeff - h)) > 0.15:
            # Some surveyed RoofSurface rings are folded, not planar. Keep their
            # actual vertex heights as triangular planes instead of losing the face.
            import mapbox_earcut

            rings = [np.asarray(r.coords)[:-1] for r in [surface.exterior, *surface.interiors]]
            vertices = np.concatenate(rings)
            ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
            triangles = mapbox_earcut.triangulate_float64(vertices[:, :2].copy(), ends).reshape(
                -1, 3
            )
            for triangle in triangles:
                part = vertices[triangle]
                poly = shapely.Polygon(part[:, :2])
                if poly.area < 0.01:
                    continue
                local_plane = np.linalg.solve(np.c_[part[:, :2], np.ones(3)], part[:, 2])
                for piece in polygons(poly.intersection(footprint)):
                    candidates.append(
                        (
                            float(plane_height(local_plane, piece.centroid.coords[0])),
                            piece,
                            local_plane,
                        )
                    )
        else:
            plane = np.r_[coeff[:2], coeff[2] - center @ coeff[:2]]
            for poly in polygons(
                shapely.make_valid(shapely.force_2d(surface)).intersection(footprint)
            ):
                candidates.append(
                    (float(plane_height(plane, poly.centroid.coords[0])), poly, plane)
                )
    parts, covered = [], shapely.Polygon()
    for _, poly, plane in sorted(candidates, key=lambda item: item[0], reverse=True):
        for piece in polygons(poly.difference(covered)):
            parts.append((shapely.geometry.polygon.orient(piece, 1), plane))
        covered = shapely.union_all([covered, poly])
    parts.extend(
        (p, np.array([0.0, 0.0, fallback])) for p in polygons(footprint.difference(covered))
    )
    return parts


def supported_offset(polygon, plane, points, base):
    """Equal-area cell medians reject facade returns and oversampled flight strips.

    Require broad interior support and a coherent residual before moving a surveyed
    plane. Sparse samples, mixed terraces and roof furniture cannot shift an entire face.
    """
    interior = polygon.buffer(-0.75)
    if interior.is_empty:
        return 0.0, {"source": "lod2", "cells": 0, "coverage": 0.0}
    q = points[
        shapely.contains_xy(interior, points[:, 0], points[:, 2]) & (points[:, 1] > base + 2)
    ]
    if not len(q):
        return 0.0, {"source": "lod2", "cells": 0, "coverage": 0.0}
    cells = np.floor(q[:, [0, 2]]).astype(int)
    _, inverse = np.unique(cells, axis=0, return_inverse=True)
    residual = q[:, 1] - plane_height(plane, q[:, [0, 2]])
    order = np.lexsort((residual, inverse))
    counts = np.bincount(inverse)
    starts = np.r_[0, np.cumsum(counts[:-1])]
    # Sorted per-cell middle elements give the same medians without Python loops.
    lo = starts + (counts - 1) // 2
    hi = starts + counts // 2
    medians = ((residual[order[lo]] + residual[order[hi]]) / 2)[counts >= 3]
    coverage = min(1.0, len(medians) / max(interior.area, 1))
    evidence = {"source": "lod2", "cells": len(medians), "coverage": coverage}
    if len(medians) < 8 or coverage < 0.5:
        return 0.0, evidence
    offset = float(np.median(medians))
    mad = float(np.median(np.abs(medians - offset)))
    evidence.update(offset_m=offset, residual_mad_m=mad)
    if mad > 0.35:
        return 0.0, evidence
    evidence["source"] = "class6_plane_offset"
    return offset, evidence


@lru_cache(maxsize=4)
def _class6(path, size, mtime):
    # Stat identity invalidates changed sources; the shared reader still verifies
    # content when first opening a source. Keep mmap-backed arrays between tiles.
    from turku3d.buildings.reconstruct import class6

    return class6([path], C.cache_dir("coarse-roofs"))


def refine(buildings, paths, *, points=None):
    from scipy.spatial import cKDTree

    if points is None:
        if not buildings:
            return
        bounds = np.array([b.footprint.bounds for b in buildings])
        lo, hi = bounds[:, :2].min(axis=0), bounds[:, 2:].max(axis=0)
        selected = []
        for path in paths:
            stat = path.stat()
            cloud = _class6(path, stat.st_size, stat.st_mtime_ns)
            keep = (cloud[:, 0] >= lo[0]) & (cloud[:, 0] <= hi[0])
            keep &= (cloud[:, 2] >= lo[1]) & (cloud[:, 2] <= hi[1])
            selected.append(cloud[keep])
        points = np.concatenate(selected) if selected else np.empty((0, 3))
    if not len(points):
        return
    tree = cKDTree(points[:, [0, 2]])
    for building in buildings:
        bounds = np.array(building.footprint.bounds).reshape(2, 2)
        center = bounds.mean(axis=0)
        local = points[tree.query_ball_point(center, np.linalg.norm(bounds[1] - center))]
        evidence, parts = [], []
        for polygon, plane in building.roof_parts:
            offset, record = supported_offset(polygon, plane, local, building.base)
            adjusted = plane.copy()
            adjusted[2] += offset
            if (
                plane_height(adjusted, np.asarray(polygon.exterior.coords)).min()
                <= building.base + 2
            ):
                adjusted = plane
                record["source"] = "lod2"
            parts.append((polygon, adjusted))
            evidence.append(record)
        building.roof_parts = parts
        building.height_evidence = evidence
        building.top = max(
            float(plane_height(p, np.asarray(g.exterior.coords)).max()) for g, p in parts
        )


def above(polygon, plane, height):
    """Clip a sloping roof to the part above a passage ceiling before extrusion."""
    if np.linalg.norm(plane[:2]) < 1e-12:
        return polygon if plane[2] > height else shapely.Polygon()
    x0, z0, x1, z1 = polygon.bounds
    ring = np.array([[x0 - 1, z0 - 1], [x1 + 1, z0 - 1], [x1 + 1, z1 + 1], [x0 - 1, z1 + 1]])
    clipped = []
    for p, q in zip(ring, np.roll(ring, -1, axis=0), strict=True):
        hp, hq = plane_height(plane, np.array([p, q])) - height
        if hp >= 0:
            clipped.append(p)
        if (hp >= 0) != (hq >= 0):
            clipped.append(p + (q - p) * hp / (hp - hq))
    return (
        polygon.intersection(shapely.Polygon(clipped)) if len(clipped) >= 3 else shapely.Polygon()
    )
