"""Recover red garden paving from imagery without baking shadows into its material."""

import json

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from .street_materials import BRICK


def regions(tile, rgb, surfaces, gardens, osm=None):
    from affine import Affine
    from rasterio.features import shapes
    from scipy.ndimage import binary_closing, binary_opening

    result = []
    if osm:
        from turku3d.spatial import rows
        from .garden_inputs import polygon
        for _, row in rows(osm, tile.bbox):
            tags = json.loads(row['tags'] or '{}')
            if tags.get('surface') not in ('brick', 'bricks') or tags.get('bridge', 'no') != 'no':
                continue
            if tags.get('layer', '0') not in ('0', '') or tags.get('building'):
                continue
            geom = shapely.force_2d(row['geometry_local'])
            area = polygon(geom)
            if area.is_empty and tags.get('highway') and geom.geom_type in ('LineString', 'MultiLineString'):
                try:
                    width = float(tags.get('width', 2.5))
                except ValueError:
                    width = 2.5
                area = geom.buffer(np.clip(width, .3, 10) / 2)
            if not area.is_empty:
                result.append((area, BRICK))
    if not gardens:
        return result
    hard = shapely.union_all([g for g, m in surfaces if m in
        [mat_id('road'), mat_id('paving_slab'), mat_id('sidewalk'), mat_id('cobblestone')]])
    domain = hard.intersection(shapely.union_all(gardens).buffer(12))
    if domain.is_empty:
        return result
    r, g, b = np.moveaxis(rgb.astype(float), -1, 0)
    red = (r > 65) & (r - g > 8) & (r > b * 1.12) & ((r - g) > (g - b) * 1.1)
    # Reject isolated cars/flowers and bridge narrow shadow cracks; geometry masks
    # still keep lawns and water out even when autumn foliage is reddish.
    red = binary_opening(binary_closing(red, iterations=2), iterations=1)
    scale = tile.size / rgb.shape[0]
    transform = Affine(scale, 0, tile.origin[0], 0, scale, tile.origin[2])
    for geom, value in shapes(red.astype('uint8'), mask=red, transform=transform):
        area = shapely.geometry.shape(geom).simplify(scale * .6).intersection(domain)
        if area.area >= 2:
            result.append((area, BRICK))
    return result
