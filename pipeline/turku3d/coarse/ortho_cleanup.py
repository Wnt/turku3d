"""Cheap, surface-constrained removal of aerial canopy and vehicles from ground albedo."""

import numpy as np
import shapely
from scipy import ndimage as ndi
from scipy.spatial import cKDTree

from turku3d.col.materials import mat_id

from . import surfaces

PAVED = ("road", "cycleway", "crossing", "sidewalk", "paving_slab", "cobblestone", "quay")


def canopy_points(tile, paths):
    """Use already available classified lidar; never fetch it for a draft texture."""
    if not paths:
        return np.empty((0, 3))
    from turku3d.vegetation.trees import reduced_points

    parts = []
    bounds = tile.bbox.buffered(2)
    for path in paths:
        _, canopy = reduced_points(path)
        keep = (
            (canopy[:, 0] >= bounds.xmin)
            & (canopy[:, 0] < bounds.xmax)
            & (canopy[:, 2] >= bounds.zmin)
            & (canopy[:, 2] < bounds.zmax)
        )
        parts.append(canopy[keep])
    return np.concatenate(parts)


def raster(tile, rgb, streets, regions):
    size = rgb.shape[0]
    row, col = np.indices((size, size))
    x = tile.origin[0] + (col + 0.5) * tile.size / size
    z = tile.origin[2] + (row + 0.5) * tile.size / size
    centers = np.c_[x.ravel(), np.zeros(size * size), z.ravel()]
    return surfaces.classify(centers, rgb, tile, streets, regions).reshape(size, size), x, z


def vehicle_mask(rgb, domain, scale, features):
    """Car-sized compact outliers, with a road/parking-row heading when available.

    Narrow paint and zebra bars fail the short-axis/area test. Continuous lines
    fail the long-axis test. Work at the existing draft sampling, without inference.
    """
    value = rgb.astype(float)
    radius = max(3, round(7 / scale)) | 1
    background = np.stack([ndi.median_filter(value[..., k], size=radius) for k in range(3)], -1)
    delta = np.linalg.norm(value - background, axis=-1)
    labels, _ = ndi.label(domain & (delta > 48))
    result = np.zeros(domain.shape, bool)
    for label, window in enumerate(ndi.find_objects(labels), 1):
        if window is None:
            continue
        pixels = np.argwhere(labels[window] == label)
        if not 1.8 <= len(pixels) * scale**2 <= 17:
            continue
        center = pixels.mean(0)
        _, _, axes = np.linalg.svd(pixels - center, full_matrices=False)
        projected = (pixels - center) @ axes.T
        length, width = (np.ptp(projected, axis=0) + 1) * scale
        if not (2.2 <= length <= 6.8 and 1.25 <= width <= 3.2 and 1.25 <= length / width <= 3.6):
            continue
        # Reject sparse line fragments even when their bounding box is car-sized.
        if len(pixels) * scale**2 / (length * width) < 0.45:
            continue
        if features is not None and not features(
            center + [window[0].start, window[1].start], axes[0]
        ):
            continue
        result[window] |= labels[window] == label
    return ndi.binary_dilation(result, iterations=1) & domain


def headings(tile, size, features):
    lines = []
    for feature in features:
        tags, geom = feature.tags, feature.geometry
        if tags.get("highway") and geom.geom_type == "LineString":
            coords = np.asarray(geom.coords)[:, :2]
            for a, b in zip(coords[:-1], coords[1:], strict=True):
                if np.linalg.norm(b - a) > 1:
                    lines.append((shapely.LineString([a, b]), (b - a) / np.linalg.norm(b - a)))
    if not lines:
        return None
    tree = shapely.STRtree([g for g, _ in lines])

    def aligned(center, axis):
        point = shapely.Point(
            tile.origin[0] + (center[1] + 0.5) * tile.size / size,
            tile.origin[2] + (center[0] + 0.5) * tile.size / size,
        )
        index = tree.nearest(point)
        if lines[index][0].distance(point) > 30:
            return False
        angle = np.arccos(np.clip(abs(np.dot(axis[::-1], lines[index][1])), 0, 1))
        # Parallel travel/parking, perpendicular bays and diagonal parking rows.
        return min(abs(angle - a) for a in (0, np.pi / 4, np.pi / 2)) < np.deg2rad(18)

    return aligned


def clean(
    tile,
    rgb,
    streets,
    regions,
    canopy,
    heights,
    markings=(),
    buildings=(),
    water=(),
    parking=((), ()),
):
    ids, x, z = raster(tile, rgb, streets, regions)
    scale = tile.size / rgb.shape[0]
    paved = np.isin(ids, [mat_id(k) for k in PAVED])
    blocked = shapely.union_all([*buildings, *water])
    if not blocked.is_empty:
        paved &= ~shapely.intersects_xy(blocked, x, z)
    r, g, b = np.moveaxis(rgb.astype(float), -1, 0)
    green = (g > r * 1.08) & (g > b * 1.12) & (g > 22)
    lidar = np.zeros(ids.shape, bool)
    if len(canopy):
        distance, index = cKDTree(canopy[:, [0, 2]]).query(np.c_[x.ravel(), z.ravel()])
        ground = ndi.map_coordinates(
            heights,
            [
                (z.ravel() - tile.origin[2]) / tile.size * (heights.shape[0] - 1),
                (x.ravel() - tile.origin[0]) / tile.size * (heights.shape[1] - 1),
            ],
            order=1,
        )
        lidar = ((distance < 0.9) & (canopy[index, 1] - ground > 1.5)).reshape(ids.shape)
    # Dark foliage backup needs adjacent green evidence, so asphalt shadows alone stay.
    dark_foliage = (rgb.max(-1) < 65) & ndi.maximum_filter(green, size=max(3, round(2 / scale)))
    canopy_mask = paved & (lidar | green | dark_foliage)
    road = paved & np.isin(ids, [mat_id(k) for k in ("road", "cycleway", "crossing")])
    lots = [
        f.geometry
        for f in parking[0]
        if f.tags.get("amenity") == "parking"
        and f.tags.get("parking") not in ("underground", "multi-storey")
    ]
    if lots:
        road |= paved & shapely.intersects_xy(shapely.union_all(lots), x, z)
    cars = vehicle_mask(rgb, road & ~canopy_mask, scale, headings(tile, rgb.shape[0], parking[0]))
    paint = shapely.union_all(markings)
    if not paint.is_empty:
        cars &= ~shapely.intersects_xy(paint, x, z)
    mask = canopy_mask | cars
    output = rgb.copy()
    stats = {}
    for name in PAVED:
        surface = ids == mat_id(name)
        target = surface & mask
        n = int(surface.sum())
        if not n:
            continue
        donors = surface & paved & ~mask & (rgb.max(-1) > 20) & (rgb.min(-1) < 205)
        if target.any():
            channel = surfaces.BINDINGS[name]
            fallback = surfaces.fallback(channel)
            iy = np.floor(z[target] * 128).astype(int) % 256
            ix = np.floor(x[target] * 128).astype(int) % 256
            output[target] = fallback[iy, ix]
            if donors.sum() >= 32:
                distance, nearest = ndi.distance_transform_edt(~donors, return_indices=True)
                nearby = target & (distance * scale <= 12)
                output[nearby] = rgb[nearest[0][nearby], nearest[1][nearby]]
        stats[name] = dict(
            pixels=n,
            canopy=int((surface & canopy_mask).sum()),
            vehicles=int((surface & cars).sum()),
            masked_share=float(target.sum() / n),
        )
    return output, stats
