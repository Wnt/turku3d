"""Forest floors: under closed canopy inside mapped woodland the ground is leaf litter, needle duff or moss, not lawn.

City-wide rules, all from data:
- woodland: the OSM landuse=forest and natural=wood areas (``forests.py`` kinds ``forest`` and ``wood``);
- closed canopy: at least COVER of the ground within RADIUS m lies under lidar canopy over CANOPY_MIN m (class-5
  returns, the same crop the sky bake reads, so both sides of a tile border decide alike);
- moss where the ground is wet: within SHORE m of water, or a terrain hollow HOLLOW m below its surroundings;
- otherwise needle duff where most trees within RADIUS are conifers, leaf litter where most are broadleaf (the
  trees' leaf type is the register's species or the lidar's measured foliage, ``canopy_trees.leaf_type_at``); with
  no tree within RADIUS the woodland's ``leaf_type`` tag decides.
- parks and gardens (``forests.py`` kinds ``park`` and ``garden``) under dense tree groups: PARK_COVER of the ground
  within PARK_RADIUS m under canopy. The mower stops at the group's edge and the shade thins the lawn to litter and
  bare soil up to the trunks (Puolanpuisto, Samppalinna panoramas); lawn under single park crowns stays.
Open woodland (glades, sparse pine on rock, lawns under single park crowns) keeps its grass; mapped paths, paving,
rock and the terrain stage's natural ground keep their own surfaces. Collision keeps the physical ground class:
the floors are visual finishes (``street_materials``).
"""
import numpy as np
import shapely

from .forest_paths import polygonal
from .street_materials import FOREST_BROADLEAF, FOREST_CONIFER, FOREST_MOSS

KINDS = ("forest", "wood")
PARKS = ("park", "garden")
PARK_RADIUS = 6.0  # m
PARK_COVER = 0.8
CELL = 2.0  # m
RADIUS = 10.0  # m
COVER = 0.5
CANOPY_MIN = 5.0  # m above ground
SHORE = 15.0  # m
HOLLOW = 0.5  # m below the mean ground within about 15 m
MIN_AREA = 30.0  # m2
CONIFERS = ("tree_pineDefaultA",)


def _disk(radius):
    r = int(np.ceil(radius / CELL))
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y <= (radius / CELL) ** 2).astype(float)


def _share(mask, kernel):
    from scipy.ndimage import convolve

    return convolve(mask.astype(float), kernel, mode="nearest") / kernel.sum()


def regions(tile, forests, canopy, height_at, trees, water, excluded=None):
    """``[(polygon, finish id)]`` of the forest floors in ``tile``; empty without woodland or measured canopy."""
    from rasterio.features import shapes
    from affine import Affine
    from scipy.ndimage import gaussian_filter

    window = shapely.box(*tile.bbox.as_tuple())
    woods = [(geom, leaf) for _, geom, kind, leaf in forests if kind in KINDS and geom.intersects(window)]
    parks = [geom for _, geom, kind, _ in forests if kind in PARKS and geom.intersects(window)]
    points = np.asarray(canopy if canopy is not None else (), float).reshape(-1, 3)
    if not (woods or parks) or not len(points):
        return []
    woodland = shapely.union_all([g for g, _ in woods]).intersection(window) if woods else shapely.Polygon()
    parkland = shapely.union_all(parks).intersection(window).difference(woodland) if parks else shapely.Polygon()
    if excluded is not None and not excluded.is_empty:
        woodland, parkland = woodland.difference(excluded), parkland.difference(excluded)
    woodland, parkland = polygonal(woodland), polygonal(parkland)
    if woodland.is_empty and parkland.is_empty:
        return []
    # Grid over the tile plus the canopy radius, cell centres at x0 + (i + 0.5) * CELL.
    pad = int(np.ceil(RADIUS / CELL))
    n = int(round(tile.size / CELL)) + 2 * pad
    x0, z0 = tile.bbox.xmin - pad * CELL, tile.bbox.zmin - pad * CELL
    xs = x0 + (np.arange(n) + .5) * CELL
    gx, gz = np.meshgrid(xs, z0 + (np.arange(n) + .5) * CELL)  # [row = z, col = x]
    ground = height_at.many(gx.ravel(), gz.ravel()).reshape(n, n)

    def raster(x, z, keep=None):
        i = np.floor((np.asarray(z) - z0) / CELL).astype(int)
        j = np.floor((np.asarray(x) - x0) / CELL).astype(int)
        ok = (i >= 0) & (i < n) & (j >= 0) & (j < n)
        if keep is not None:
            ok &= keep
        counts = np.zeros((n, n))
        np.add.at(counts, (i[ok], j[ok]), 1)
        return counts

    ci = np.clip(np.floor((points[:, 2] - z0) / CELL).astype(int), 0, n - 1)
    cj = np.clip(np.floor((points[:, 0] - x0) / CELL).astype(int), 0, n - 1)
    above = points[:, 1] - ground[ci, cj]
    canopy_cells = raster(points[:, 0], points[:, 2], (above > CANOPY_MIN) & (above < 40)) > 0
    disk = _disk(RADIUS)
    closed = _share(canopy_cells, disk) >= COVER
    grove = _share(canopy_cells, _disk(PARK_RADIUS)) >= PARK_COVER
    if not closed.any():
        return []
    # Species: the trees placed around the cell, else the woodland's OSM leaf_type.
    xy = np.array([(t.x, t.z) for t in trees]).reshape(-1, 2)
    conifer_tree = np.array([t.variant in CONIFERS for t in trees], bool)
    total = _share(raster(xy[:, 0], xy[:, 1]), disk)
    conifer = np.where(total > 0, _share(raster(xy[:, 0], xy[:, 1], conifer_tree), disk) / np.maximum(total, 1e-9), 0)
    needles = conifer >= .5
    for geom, leaf in woods:
        if leaf in ("needleleaved", "broadleaved"):
            inside = shapely.contains_xy(geom, gx, gz) & (total <= 0)
            needles[inside] = leaf == "needleleaved"
    wet = ground - gaussian_filter(ground, 7.5 / CELL, mode="nearest") < -HOLLOW
    shores = [w for w in water if not w.is_empty]
    if shores:
        wet |= shapely.contains_xy(shapely.union_all(shores).buffer(SHORE), gx, gz)
    floor = np.where(wet, FOREST_MOSS, np.where(needles, FOREST_CONIFER, FOREST_BROADLEAF))
    classes = np.where(closed, floor, 0)
    groves = np.where(grove, floor, 0)[pad:n - pad, pad:n - pad].astype(np.uint8)
    classes = classes[pad:n - pad, pad:n - pad].astype(np.uint8)
    transform = Affine(CELL, 0, tile.bbox.xmin, 0, CELL, tile.bbox.zmin)
    result = []
    for finish in (FOREST_BROADLEAF, FOREST_CONIFER, FOREST_MOSS):
        pieces = []
        for grid, land in ((classes, woodland), (groves, parkland)):
            mask = grid == finish
            if land.is_empty or not mask.any():
                continue
            parts = [shapely.geometry.shape(geo).simplify(CELL / 2)
                     for geo, v in shapes(mask.astype(np.uint8), mask=mask, transform=transform) if v]
            pieces.append(polygonal(shapely.union_all(parts).intersection(land)))
        area = shapely.union_all([p for piece in pieces for p in shapely.get_parts(piece) if p.area >= MIN_AREA])
        if not area.is_empty:
            result.append((area, finish))
    return result
