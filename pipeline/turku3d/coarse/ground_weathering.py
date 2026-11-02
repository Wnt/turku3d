"""Restrained street wear following surveyed kerbs; visual geometry only.

The existing opaque ground carries colour variation, without decal sorting or
another ground draw. Grates are one small opaque mesh. Their locations are
procedural street detail, not claims of surveyed municipal drain positions.
"""
import hashlib

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh
from .walkable import coordinates

ASPHALT_REPAIR = 130  # Visual-only id; uses the existing fresh asphalt library.


def details(profiles):
    """Stable road-aligned patches and drains, away from the ends of fitted kerbs."""
    repairs, drains = [], []
    for profile in sorted(profiles, key=lambda p: p['key']):
        line = profile['line']
        if line.length < 4:
            continue
        seed = int.from_bytes(hashlib.sha256(profile['key'].split(':')[0].encode()).digest()[:4], 'little')
        source = profile['source_stations']
        knots = profile['knots']
        order = np.argsort(source)
        begin, end = float(np.min(source)), float(np.max(source))
        phase = seed % 25
        events = np.arange(np.ceil((begin - phase) / 25), np.floor((end - phase) / 25) + 1, dtype=int)
        for event in events:
            station = np.interp(event * 25 + phase, source[order], knots[order])
            if station < 1 or station > line.length - 1:
                continue
            centre = np.asarray(line.interpolate(station).coords[0])
            along = (np.asarray(line.interpolate(station + .2).coords[0])
                     - np.asarray(line.interpolate(station - .2).coords[0]))
            along /= max(np.linalg.norm(along), 1e-9)
            left = np.array([-along[1], along[0]])
            drains.append((centre - left * .29, along, left))
            # Occasional narrow utility repair, rather than a road-wide stripe.
            if (event + seed) % 3 == 0:
                c = centre - left * (1.05 + (seed % 7) * .08)
                ring = np.array([[-.8, -.25], [.65, -.25], [.85, -.1],
                                 [.85, .3], [-.65, .3], [-.8, .15]])
                repairs.append(shapely.Polygon(c + ring[:, :1] * along + ring[:, 1:] * left))
    return repairs, drains


def surface(positions, indices, materials, profiles, normals=None):
    """Insert narrow wear bands without changing a single terrain plane."""
    from .surfaces import split_boundaries

    repairs, drains = details(profiles)
    road = np.isin(materials, [mat_id('road'), mat_id('crossing')])
    if repairs and road.any():
        p, ix, ids, normals = split_boundaries(positions, indices, materials, road,
                                       [(g, ASPHALT_REPAIR) for g in repairs], normals=normals)
        indices = np.concatenate([indices[~road], ix])
        materials = np.concatenate([materials[~road], ids])
        positions = p
    bands = [(p['line'].buffer(width, single_sided=True, join_style='mitre', mitre_limit=2), 0)
             for p in profiles for width in (-.35, -.12, .35)]
    hard_ids = [mat_id(name) for name in ('road', 'crossing', 'sidewalk', 'curb',
                                        'cobblestone', 'paving_slab')] + [128, 129, ASPHALT_REPAIR]
    selected = np.isin(materials, hard_ids)
    p, ix, ids, normals = split_boundaries(positions, indices, materials, selected, bands,
                                          preserve_materials=True, normals=normals)
    ix = np.concatenate([indices[~selected], ix])
    ids = np.concatenate([materials[~selected], ids])
    colours = np.ones((len(p), 3), np.float32)
    hard = np.isin(ids, hard_ids)
    active = np.zeros(len(p), bool)
    active[np.unique(ix[hard])] = True
    dirt = np.zeros(len(p))
    active_ids = np.flatnonzero(active)
    tree = shapely.STRtree(shapely.points(p[active_ids][:, [0, 2]]))
    for profile in profiles:
        line = profile['line']
        near = active_ids[tree.query(line, predicate='dwithin', distance=.36)]
        if not len(near):
            continue
        station, lateral = coordinates(line, p[near][:, [0, 2]])
        ends = np.clip(np.minimum(station, line.length - station), 0, 1)
        low = np.clip(1 - np.maximum(-lateral - .025, 0) / .30, 0, 1) * (lateral < .015)
        high = np.clip(1 - np.maximum(lateral - .015, 0) / .20, 0, 1) * (lateral >= .015)
        # Broken accumulation, never a black continuous outline around the road.
        x, z = p[near, 0], p[near, 2]
        variation = .6 + .2 * np.sin(x * 1.7 + z * .9) + .2 * np.sin(x * .37 - z * .51)
        strength = (low * .28 + high * .12) * ends * variation
        np.maximum.at(dirt, near, strength)
    colours *= 1 - dirt[:, None] * np.array([.88, .91, 1.0])
    return p, ix, ids, normals, colours, drains


def grates(tile, positions, indices, materials, drains):
    """One batched cast-iron mesh, seated on the actual road triangles."""
    road = np.isin(materials, [mat_id(n) for n in ('road', 'crossing', 'cobblestone')]
                   + [128, ASPHALT_REPAIR])
    if not drains or not road.any():
        return []
    triangles = positions[indices[road]]
    footprints = shapely.polygons(triangles[:, :, [0, 2]])
    tree = shapely.STRtree(footprints)
    window = shapely.box(*tile.bbox.as_tuple())
    points, faces, colours = [], [], []

    def quad(xz, lift, rgb):
        import mapbox_earcut

        polygon = shapely.Polygon(xz).intersection(window)
        for index in tree.query(polygon, predicate='intersects'):
            if footprints[index].area < 1e-10:
                continue
            clipped = polygon.intersection(footprints[index])
            tri = triangles[index]
            plane = np.linalg.solve(np.c_[tri[:, [0, 2]], np.ones(3)], tri[:, 1])
            for part in shapely.get_parts(clipped):
                if part.geom_type != 'Polygon' or part.area < 1e-10:
                    continue
                xz = np.asarray(part.exterior.coords)[:-1]
                ix = mapbox_earcut.triangulate_float64(xz, np.array([len(xz)], np.uint32)).reshape(-1, 3)
                p = np.c_[xz[:, 0], np.c_[xz, np.ones(len(xz))] @ plane + lift, xz[:, 1]]
                winding = np.cross(p[ix[:, 1]] - p[ix[:, 0]], p[ix[:, 2]] - p[ix[:, 0]])[:, 1] < 0
                ix[winding] = ix[winding][:, [0, 2, 1]]
                faces.extend(ix + len(points))
                points.extend(p)
                colours.extend([rgb] * len(p))

    for centre, along, left in drains:
        def rectangle(a, b, c, d):
            return centre + np.array([[a, c], [b, c], [b, d], [a, d]]) @ np.array([along, left])
        footprint = shapely.Polygon(rectangle(-.25, .25, -.18, .18))
        # Neighbours emit their own portion of the same canonical grate.
        local = footprint.intersection(window)
        if local.is_empty or local.area < 1e-10:
            continue
        area = shapely.union_all(footprints[tree.query(local, predicate='intersects')])
        if local.difference(area).area > 1e-9:
            continue
        quad(rectangle(-.25, .25, -.18, .18), .002, (.018, .021, .023))
        for rect in [(-.25, -.225, -.18, .18), (.225, .25, -.18, .18),
                     (-.225, .225, -.18, -.155), (-.225, .225, .155, .18)]:
            quad(rectangle(*rect), .006, (.095, .10, .10))
        for a in np.arange(-.20, .21, .055):
            quad(rectangle(a, a + .022, -.155, .155), .006, (.075, .08, .08))
    if not faces:
        return []
    return [GlbMesh(f'ground_drains_{tile.id}', np.asarray(points) - tile.origin,
                    np.asarray(faces, np.uint32), colors=np.asarray(colours, np.float32),
                    color=(1, 1, 1, 1), roughness=.82,
                    extras=dict(layer='ground', material_key='drain_iron',
                                object_id=f'ground_drains_{tile.id}'))]
