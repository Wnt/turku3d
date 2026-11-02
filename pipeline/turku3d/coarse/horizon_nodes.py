"""Source-derived kilometre nodes: three merged layers, never full street tiles."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import mapbox_earcut
import numpy as np
import shapely

from turku3d import config as C
from turku3d.buildings.source_surfaces import classify_surfaces
from turku3d.spatial import frame
from . import forests, inputs
from .sources import NoCoverage, block_of
from .horizon_identity import source_key


def describe(service, tile):
    from .horizon import digest
    blocks, files = [], set()
    for i, j in C.grid_range(tile.bbox, 500):
        block = C.VisualTile(1, i, j)
        # Actual cached survey inputs decide coverage; the city's download index
        # omits boundary blocks even when the immutable overlay contains their data.
        # WFS summaries may cover each quadrant separately; preserve each complete feature.
        buildings, water, streets, osm = set(), set(), set(), set()
        complete = True
        for leaf in block.children():
            found, source = service.sources.wfs_source('bldg:Building_LOD2', leaf)
            complete &= found
            if source: buildings.add(str(source))
            for layer, paths in [('kanta:Vesikohde', water), ('GIS:Katualueet', streets)]:
                _, source = service.sources.wfs_source(layer, leaf)
                if source: paths.add(str(source))
            source = service.sources.osm_source(leaf)
            if source: osm.add(str(source))
        try:
            ground, missing = service.sources.lidar_paths(block)
        except NoCoverage:
            ground, missing = [], [block]
        if missing or not ground or not complete:
            regional = service.regional_row(block)
            if regional:
                blocks.append(dict(tile=block.id,regional=service.regional,buildings=[]))
                files.add(service.regional)
            continue
        row = dict(tile=block.id, ground=list(map(str, ground)), buildings=sorted(buildings),
                   water=sorted(water), streets=sorted(streets), osm=sorted(osm))
        blocks.append(row)
        for paths in (row[k] for k in ('ground', 'buildings', 'water', 'streets', 'osm')):
            files.update(paths)
    if not blocks:
        return None
    hashes = {p: digest(p) for p in sorted(files)}
    # Coverage is part of the identity, including empty source blocks at the survey edge.
    key = source_key(service.recipe, tile.id, blocks, hashes)
    return dict(tile=tile.id, key=key, source_sha256=key, kind='survey', recipe=service.recipe,
                inputs=blocks, hashes=hashes, h=[0, 180], coverage=len(blocks) / (tile.size / 500)**2)


def pigment(key, group, appearance):
    from PIL import ImageColor
    keys = [str(key), 'gis-' + str(key)]
    if 'pysyvarakennustunnus' in group:
        keys.insert(0, str(group.pysyvarakennustunnus.iloc[0]))
    colour = next((appearance[k] for k in keys if k in appearance), None)
    try:
        rgb = np.asarray(ImageColor.getrgb(colour)[:3]) / 255
        return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055)**2.4)
    except (ValueError, TypeError, AttributeError):
        return np.array([.55, .51, .43])


def face_triangles(polygon):
    """Triangulate a surveyed planar face on its dominant plane, retaining holes."""
    rings = [np.asarray(r.coords)[:-1][:, [0, 2, 1]] for r in [polygon.exterior, *polygon.interiors]]
    p = np.concatenate(rings)
    if len(p) < 3 or not np.isfinite(p).all():
        return p, np.empty((0, 3), np.uint32)
    normal = np.sum(np.cross(rings[0] - rings[0][0], np.roll(rings[0], -1, axis=0) - rings[0][0]), axis=0)
    if np.linalg.norm(normal) < 1e-8:
        return p, np.empty((0, 3), np.uint32)
    axes = [i for i in range(3) if i != np.argmax(abs(normal))]
    indices = mapbox_earcut.triangulate_float64(np.ascontiguousarray(p[:, axes]),
                                               np.cumsum([len(r) for r in rings], dtype=np.uint32)).reshape(-1, 3)
    return p, indices


def survey_shells(tile, paths, appearance):
    from .horizon import layer_mesh
    positions, faces, colours, owned, footprints = [], [], [], set(), []
    for path in sorted(paths):
        data = classify_surfaces(frame(path, tile.bbox.buffered(100), whole_features=True))
        for key, group in data.groupby('feature_key', sort=True):
            if key in owned: continue
            floors = group[group.surface_type == 'GroundSurface']
            footprint = shapely.make_valid(shapely.union_all(shapely.force_2d(floors.geometry_local.values)))
            parts = [p for p in shapely.get_parts(footprint) if p.geom_type == 'Polygon']
            if not parts: continue
            centre = min(parts, key=lambda p: (-p.area, p.wkb)).centroid
            if not tile.bbox.contains(centre.x, centre.y): continue
            owned.add(key); footprints.append(footprint)
            wall_colour = pigment(key, group, appearance)
            for row in group.itertuples():
                if row.surface_type == 'GroundSurface': continue
                p, ix = face_triangles(row.geometry_local)
                if not len(ix): continue
                normal = np.cross(p[ix[:, 1]] - p[ix[:, 0]], p[ix[:, 2]] - p[ix[:, 0]])
                if row.surface_type == 'RoofSurface':
                    flip = normal[:, 1] < 0
                else:
                    length = np.linalg.norm(normal[:, [0, 2]], axis=1)
                    mid = p[ix].mean(axis=1)
                    # The outward side must lie outside the footprint, including courtyard walls.
                    probe = mid[:, [0, 2]] + normal[:, [0, 2]] / np.maximum(length[:, None], 1e-9) * .05
                    flip = shapely.contains_xy(footprint, probe[:, 0], probe[:, 1])
                ix[flip] = ix[flip][:, [0, 2, 1]]
                faces.extend(ix + len(positions)); positions.extend(p)
                colours.extend([(.20, .23, .22) if row.surface_type == 'RoofSurface' else wall_colour] * len(p))
    mesh = layer_mesh('buildings', positions, faces, colours, np.asarray(tile.origin)) if faces else None
    return mesh, len(owned), shapely.union_all(footprints)


def sample_heights(points, xs, zs):
    """World-space nearest-neighbour interpolation, independent of node triangulation."""
    from scipy.spatial import cKDTree
    points = np.unique(points, axis=0)
    gx, gz = np.meshgrid(xs, zs)
    query = np.c_[gx.ravel(), gz.ravel()]
    tree = cKDTree(points[:, [0, 2]])
    distance, _ = tree.query(query, k=min(8, len(points)))
    support = np.minimum(100.000001, np.atleast_2d(distance)[:, -1] + 1e-6)
    neighbourhoods = tree.query_ball_point(query, r=support)
    heights = []
    for q, indices in zip(query, neighbourhoods):
        if not indices:
            indices = tree.query_ball_point(q, r=500.000001)
        if not indices:
            # Very flat survey blocks may have key points only beyond the support disk.
            # Use the nearest measured height from the declared, hashed neighbouring files.
            _, nearest = tree.query(q, k=1)
            indices = [int(nearest)]
        candidates = points[indices]
        distance = np.linalg.norm(candidates[:, [0, 2]] - q, axis=1)
        # cKDTree may choose different equidistant neighbours for independently cropped halos.
        # Include the full support disk, then break ties by world coordinates before selecting.
        order = np.lexsort((candidates[:, 1], candidates[:, 2], candidates[:, 0], distance))[:8]
        weights = 1 / np.maximum(distance[order], .01)**2
        heights.append(np.sum(candidates[order, 1] * weights) / weights.sum())
    return np.asarray(heights).reshape(gx.shape)



def block_ground(block, paths, far, buildings):
    from .horizon import layer_mesh, polygons
    # Read a fixed 500 m halo; sparse key-point surveys need wider support than dense terrain.
    query = SimpleNamespace(bbox=block.bbox.buffered(480), id=block.id)
    points = inputs.ground_points(query, list(map(Path, paths['ground'])))
    count = 8 if far else 16
    xs = np.linspace(block.origin[0], block.origin[0] + 500, count + 1)
    zs = np.linspace(block.origin[2], block.origin[2] + 500, count + 1)
    heights = sample_heights(points, xs, zs)
    gx, gz = np.meshgrid(xs, zs)
    p = np.c_[gx.ravel(), heights.ravel(), gz.ravel()]
    ix = []
    for z in range(count):
        for x in range(count):
            a = z * (count + 1) + x
            ix.extend([[a, a + count + 1, a + 1], [a + 1, a + count + 1, a + count + 2]])
    p = p[np.asarray(ix)].reshape(-1, 3)
    centres = p.reshape(-1, 3, 3).mean(axis=1)
    water = shapely.union_all([polygons(path, block) for path in paths['water']])
    streets = shapely.union_all([polygons(path, block) for path in paths['streets']])
    areas = [area for path in paths['osm'] for area in forests.read_areas(block, Path(path))]
    woods = shapely.union_all([g for _, g, kind, _ in areas if kind in ('wood', 'forest', 'scrub')])
    colour = np.tile((.24, .29, .16), (len(centres), 1))
    colour[shapely.contains_xy(woods, centres[:, 0], centres[:, 2])] = (.12, .19, .08)
    colour[shapely.contains_xy(streets, centres[:, 0], centres[:, 2])] = (.19, .20, .20)
    colour[shapely.contains_xy(water, centres[:, 0], centres[:, 2])] = (.10, .19, .24)
    mesh = layer_mesh('ground', p, np.arange(len(p)).reshape(-1, 3), np.repeat(colour, 3, axis=0), np.zeros(3))
    from scipy.interpolate import RegularGridInterpolator
    interpolate = RegularGridInterpolator((zs, xs), heights)
    # Fixed world-grid clusters: stable across level changes, blocked by water and surveyed streets.
    canopy = clusters(block, woods.difference(shapely.union_all([water.buffer(8), streets.buffer(8), buildings.buffer(6)])), interpolate, far)
    return mesh, canopy


def clusters(block, woods, height, far):
    from .horizon import layer_mesh
    step = 48 if far else 24
    positions, faces = [], []
    boundary = woods.boundary
    for i in range(int(np.floor(block.bbox.xmin / step)) - 1, int(np.ceil(block.bbox.xmax / step)) + 1):
        for j in range(int(np.floor(block.bbox.zmin / step)) - 1, int(np.ceil(block.bbox.zmax / step)) + 1):
            # Shared anchors keep their height and offset when the outer level uses every second anchor.
            seed = hashlib.sha256(f'{i * step}:{j * step}'.encode()).digest()
            x, z = i * step + (seed[0] / 255 - .5) * 14, j * step + (seed[1] / 255 - .5) * 14
            if not block.bbox.contains(x, z) or not shapely.contains_xy(woods, x, z): continue
            clearance = shapely.distance(shapely.Point(x, z), boundary)
            radius = min(step * (.45 + seed[2] / 255 * .25), clearance * .98)
            if radius < 2: continue
            y = float(height([[z, x]])[0])
            top = 10 + seed[3] / 255 * 14
            base = len(positions)
            positions.append([x, y + top, z])
            angle = seed[4] / 255 * np.pi * 2
            for side in range(4):
                theta = angle + side * np.pi / 2
                reach = radius * (.75 + seed[5 + side] / 255 * .25)
                positions.append([x + np.cos(theta) * reach, y + top * .55, z + np.sin(theta) * reach])
            positions.append([x, y + 3, z])
            faces.extend(np.array([[0, 2, 1], [0, 3, 2], [0, 4, 3], [0, 1, 4],
                                  [5, 1, 2], [5, 2, 3], [5, 3, 4], [5, 4, 1]]) + base)
    return layer_mesh('vegetation', positions, faces, [(.28, .37, .13)] * len(positions), np.zeros(3)) if faces else None


def build_node(tile, blocks, appearance):
    from .horizon import layer_mesh
    meshes, buildings, footprints = survey_shells(tile, {p for row in blocks for p in row['buildings']}, appearance)
    groups = {'ground': [], 'vegetation': []}
    for row in blocks:
        if 'regional' in row:
            from .horizon_regional import mesh
            block = C.VisualTile.parse(row['tile'])
            ground, trees = mesh(block,row['regional']), None
            ground.positions += np.asarray(block.origin)
        else:
            ground, trees = block_ground(C.VisualTile.parse(row['tile']), row, tile.level >= 4, footprints)
        groups['ground'].append(ground)
        if trees: groups['vegetation'].append(trees)
    result = [meshes] if meshes else []
    for layer, parts in groups.items():
        if not parts: continue
        offsets = np.cumsum([0, *[len(p.positions) for p in parts[:-1]]])
        result.append(layer_mesh(layer, np.concatenate([p.positions for p in parts]),
                                 np.concatenate([p.indices + offset for p, offset in zip(parts, offsets)]),
                                 np.concatenate([p.colors for p in parts]), np.asarray(tile.origin)))
    return result, dict(buildings=buildings, triangles=sum(len(m.indices) for m in result), draws=len(result),
                        covered_blocks=len(blocks), total_blocks=int((tile.size / 500)**2))
