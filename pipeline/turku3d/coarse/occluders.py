"""Conservative opaque occluder boxes for whole-building models (GTA-style box occluders).

Boxes lie strictly inside the LoD2 volume: inset from the roofed footprint, from one
storey above the base to below the lowest roof point, with surveyed through-passages
removed as whole columns. A model only receives boxes when every emitted surface is
opaque (no alpha, no glass), so no box can hide anything visible through a window,
glazed facade, courtyard, passage or open ground storey. Model bounds never become
occluders. Derived from the perfcpu draft (lod2-inset-boxes-v1).
"""

import math

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import box

PROVENANCE = "lod2-inset-boxes-v1"
INSET = 0.25
GROUND_STOREY = 4.0
MIN_SIZE = 2.0
MAX_BOXES = 8


def opaque(meshes):
    """True when no emitted surface can be seen through."""
    for mesh in meshes:
        if len(mesh.color) > 3 and mesh.color[3] < 1:
            return False
        names = " ".join(str(mesh.extras.get(k, "")) for k in ("material_key", "material", "surface"))
        if "glass" in names.lower() or "glaz" in names.lower():
            return False
    return bool(meshes)


def orientation(footprint):
    """Yaw (radians, about +y, x towards z) of the footprint's longest rotated-rectangle edge."""
    ring = np.asarray(footprint.minimum_rotated_rectangle.exterior.coords)
    edges = np.diff(ring[:3], axis=0)
    edge = edges[np.argmax(np.linalg.norm(edges, axis=1))]
    return float(math.atan2(edge[1], edge[0]) % math.pi) if np.linalg.norm(edge) > 1e-9 else 0.0


def _rectangles(interior, step):
    """Merged axis-aligned grid rectangles fully covered by `interior` (xz)."""
    min_x, min_z, max_x, max_z = interior.bounds
    while math.ceil((max_x - min_x) / step) * math.ceil((max_z - min_z) / step) > 4096:
        step *= 2
    nx, nz = math.ceil((max_x - min_x) / step), math.ceil((max_z - min_z) / step)
    # Exact boundary cells must not fail on rotation round-off (0.1 mm inside a 0.25 m inset).
    tolerant = interior.buffer(1e-4)
    shapely.prepare(tolerant)
    active, rectangles = {}, []
    for z in range(nz):
        spans, start = [], None
        for x in range(nx + 1):
            inside = x < nx and tolerant.covers(box(
                min_x + x * step, min_z + z * step,
                min(min_x + (x + 1) * step, max_x), min(min_z + (z + 1) * step, max_z),
            ))
            if inside and start is None:
                start = x
            if not inside and start is not None:
                spans.append((start, x)); start = None
        following = {}
        for span in spans:
            following[span] = active.pop(span, z)
        rectangles.extend((span, first, z) for span, first in active.items())
        active = following
    rectangles.extend((span, first, nz) for span, first in active.items())
    for (left, right), front, back in rectangles:
        rect = box(min_x + left * step, min_z + front * step,
                   min(min_x + (right) * step, max_x), min(min_z + back * step, max_z))
        if tolerant.covers(rect):
            yield rect


def shell_boxes(building, shell, origin):
    """Model-local boxes of one LoD2 part; any doubtful evidence yields none.

    Each box top lies below every roof triangle above its footprint, so pitched roofs
    and low annexes keep tall boxes under their high parts (highest band first).
    """
    if not isinstance(shell, tuple) or len(shell) != 7 or shell[0] is None or shell[6] is None:
        return []
    roof, indices, walls = np.asarray(shell[0]), np.asarray(shell[1]), np.asarray(shell[6])
    if not len(indices) or not len(walls):
        return []
    triangles = roof[indices.reshape(-1, 3)]
    if not np.isfinite(triangles).all():
        return []
    use = str(getattr(building, "attributes", {}).get("use", "")).lower()
    if any(word in use for word in ("greenhouse", "canopy", "katos", "kasvihuone")):
        return []
    bottom = float(building.base) + GROUND_STOREY + INSET
    if not math.isfinite(bottom):
        return []
    origin = np.asarray(origin, float)
    yaw = orientation(building.footprint)
    # Work in the footprint's own frame so boxes follow its walls (yaw about +y).
    def local(geometry):
        return affinity.rotate(affinity.translate(geometry, -origin[0], -origin[2]), -yaw, origin=(0, 0),
                               use_radians=True)
    polygons = [local(shapely.Polygon(t[:, [0, 2]])) for t in triangles]
    lows = triangles[:, :, 1].min(axis=1) - INSET
    keep = [i for i, poly in enumerate(polygons) if poly.area > 1e-6 and lows[i] - bottom >= MIN_SIZE]
    if not keep:
        return []
    interior = building.footprint.buffer(-INSET)
    from turku3d.facade_apertures import volumes
    from turku3d.facade_programs import measured

    for polygon, _, _ in volumes(measured(building.facade_key)):
        interior = interior.difference(polygon.buffer(INSET))
    if interior.is_empty or not interior.is_valid:
        return []
    interior = local(interior)
    # Quarter-metre height bands: a band's region is roof everywhere at or above it.
    levels = sorted({math.floor(lows[i] * 4) / 4 for i in keep}, reverse=True)[:4]
    boxes, covered = [], shapely.Polygon()
    for level in levels:
        roofed = shapely.union_all([polygons[i] for i in keep if lows[i] >= level])
        region = interior.intersection(roofed.buffer(-INSET)).difference(covered)
        if region.is_empty:
            continue
        for rect in _rectangles(region, 2.0):
            x0, z0, x1, z1 = rect.bounds
            low, high = np.array([x0, bottom - origin[1], z0]), np.array([x1, float(level) - origin[1], z1])
            if np.min(high - low) < MIN_SIZE:
                continue
            boxes.append((float(np.prod(high - low)), low, high, yaw))
            covered = covered.union(rect)
    return boxes


def building_occluders(parts, meshes, origin):
    """`parts` are the model's owned (building, shell) LoD2 sources; `meshes` its final surfaces."""
    result = dict(version=1, provenance=PROVENANCE, boxes=[])
    origin = np.asarray(origin, float)
    if origin.shape != (3,) or not np.isfinite(origin).all() or not opaque(meshes):
        return result
    boxes = [b for building, shell in parts for b in shell_boxes(building, shell, origin)]
    boxes.sort(key=lambda b: -b[0])
    result["boxes"] = [dict(min=np.round(low, 3).tolist(), max=np.round(high, 3).tolist(), yaw=round(yaw, 6))
                       for _, low, high, yaw in boxes[:MAX_BOXES]]
    return result
