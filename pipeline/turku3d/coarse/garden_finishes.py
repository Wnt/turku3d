"""Unit paving finishes: explicit OSM surface tags, and red garden paving recovered from imagery
without baking shadows into its material."""

import json

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from .street_materials import BRICK, OSM_FINISHES, SAND


def beach(tags):
    """An OSM beach of sand: ``natural=beach`` (or a ``leisure=beach_resort``) whose surface is sand or untagged; a
    shingle or pebble beach keeps its own surface."""
    return (tags.get("natural") == "beach" or tags.get("leisure") == "beach_resort") and tags.get("surface") in (None, "sand")


# Metres from a sand beach to the water that stay sand: a mapped beach outline seldom reaches the waterline, and the
# strip between took the ortho's or a lawn's green (Kansanpuisto, Ruissalo: a green band between sand and sea).
SHORE = 12.0


def shore_regions(regions, water, mapped):
    """[(strip, SAND)]: land within ``SHORE`` m of both a sand finish in ``regions`` and ``water``, outside the
    water, the sand itself and ``mapped`` (paths and paving keep their surfaces)."""
    sand = shapely.union_all([g for g, f in regions if f == SAND])
    water = shapely.union_all(list(water))
    if sand.is_empty or water.is_empty:
        return []
    strip = sand.buffer(SHORE).intersection(water.buffer(SHORE)).difference(water).difference(sand)
    if mapped is not None and not mapped.is_empty:
        strip = strip.difference(mapped)
    strip = polygon_parts(strip)
    return [] if strip.is_empty else [(strip, SAND)]


def polygon_parts(geom):
    parts = [g for g in shapely.get_parts(shapely.make_valid(geom)) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def osm_regions(tile, osm):
    """Areas and ground-level ways whose OSM ``surface`` names a unit paving (brick, paving stones).

    They lie under the street register's parts, which name the surveyed material and win.
    """
    from turku3d.spatial import rows
    from .garden_inputs import polygon

    result = []
    for _, row in rows(osm, tile.bbox):
        tags = json.loads(row['tags'] or '{}')
        finish = SAND if beach(tags) else OSM_FINISHES.get(tags.get('surface'))
        if finish is None or tags.get('bridge', 'no') != 'no' or tags.get('tunnel', 'no') != 'no':
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
            area = geom.buffer(np.clip(width, .3, 10) / 2, cap_style='flat')
        if not area.is_empty:
            result.append((area, finish))
    return result


def regions(tile, rgb, surfaces, gardens):
    from affine import Affine
    from rasterio.features import shapes
    from scipy.ndimage import binary_closing, binary_opening

    result = []
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
