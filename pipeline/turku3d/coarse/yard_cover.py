"""Aerial lawn outlines inside residential parcels, and the mapped OSM play areas
(their equipment is placed by ``coarse.playgrounds``)."""

import json

import numpy as np
import shapely
from affine import Affine
from rasterio.features import shapes
from scipy.ndimage import gaussian_filter

from turku3d.col.materials import mat_id
from turku3d.spatial import rows

from .garden_inputs import polygon
from .surfaces import COVER_MIN, COVER_SMOOTH, _without_small


def features(tile, path):
    parcels, playgrounds = [], {}
    for key, row in rows(path, tile.bbox.buffered(25)):
        tags = json.loads(row['tags'] or '{}')
        area = polygon(row['geometry_local'])
        if area.is_empty:
            continue
        if tags.get('landuse') == 'residential':
            parcels.append(area)
        if tags.get('leisure') == 'playground':
            identity = str(row.get('osm_id', key))
            playgrounds[identity] = area
    return shapely.union_all(parcels), sorted(playgrounds.items())


def lawn_mask(rgb):
    r, g, b = np.moveaxis(rgb.astype(float), -1, 0)
    sun = (g > .95*r) & (g > 1.15*b) & (g > 25)
    # Blue skylight in building shade obscures the usual green/blue ratio.
    # Asphalt stays neutral in red/green, unlike a shaded lawn.
    shade = (g > 1.15*r) & (g > .8*b) & (g > 10) & (rgb.max(axis=-1) < 110)
    return sun | shade


def paving_mask(rgb):
    """Bright neutral courtyard hardscape, excluding vegetation and coloured play surfaces."""
    r, g, b = np.moveaxis(rgb.astype(float), -1, 0)
    neutral = (rgb.max(axis=-1).astype(float) - rgb.min(axis=-1)) < 42
    return (r > 120) & (g > 120) & (b > 105) & neutral & (rgb.min(axis=-1) < 248) & ~lawn_mask(rgb)


def regions(tile, rgb, parcels, blocked):
    """Keep aerial lawn and light-paving outlines that terrain centroids miss."""
    if parcels.is_empty:
        return []
    domain = parcels.difference(shapely.union_all(blocked))
    if domain.is_empty:
        return []
    step = tile.size / rgb.shape[0]
    transform = Affine(step, 0, tile.origin[0], 0, step, tile.origin[2])
    patches = []
    # The same cleaning as the ortho land cover: no speckle patches or holes, smooth edges.
    sigma, cells = COVER_SMOOTH / step, COVER_MIN / step**2
    for mask, material in ((paving_mask(rgb), "paving_slab"), (lawn_mask(rgb), "grass")):
        mask = _without_small(gaussian_filter(mask.astype(np.float32), sigma) > .5, cells).astype(np.uint8)
        for geo, value in shapes(mask, mask=mask.astype(bool), transform=transform):
            if not value:
                continue
            area = shapely.make_valid(shapely.geometry.shape(geo).simplify(step)).intersection(domain)
            for part in shapely.get_parts(area):
                if part.geom_type == 'Polygon' and part.area >= COVER_MIN:
                    patches.append((part, mat_id(material)))
    return sorted(patches, key=lambda p: (-p[0].area, p[0].wkb))
