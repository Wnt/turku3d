"""Restrained street wear following surveyed kerbs; visual geometry only.

The existing opaque ground carries colour variation, without decal sorting or
another ground draw. Grates are one small opaque mesh. Their locations are
procedural street detail, not claims of surveyed municipal drain positions.
"""
import hashlib

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.ground.mesh import HeightQuery, Mesh
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
        # World anchoring keeps the pattern stable as a tile clips a long street.
        first, last = np.asarray(line.coords)[[0, -1]]
        tangent = (last - first) / max(np.linalg.norm(last - first), 1e-9)
        if tangent[np.argmax(abs(tangent))] < 0:
            tangent = -tangent
        origin = first @ tangent
        direction = 1 if (last - first) @ tangent >= 0 else -1
        phase = origin + seed % 25
        begin, end = sorted((phase + direction, phase + direction * (line.length - 1)))
        stations = (np.arange(np.ceil(begin / 25), np.floor(end / 25) + 1) * 25 - phase) / direction
        for station in stations:
            centre = np.asarray(line.interpolate(station).coords[0])
            along = (np.asarray(line.interpolate(station + .2).coords[0])
                     - np.asarray(line.interpolate(station - .2).coords[0]))
            along /= max(np.linalg.norm(along), 1e-9)
            left = np.array([-along[1], along[0]])
            drains.append((centre - left * .29, along, left))
            # Occasional narrow utility repair, rather than a road-wide stripe.
            if (int(np.floor((origin + direction * station + seed % 25) / 25)) + seed) % 3 == 0:
                c = centre - left * (1.05 + (seed % 7) * .08)
                ring = np.array([[-.8, -.25], [.65, -.25], [.85, -.1],
                                 [.85, .3], [-.65, .3], [-.8, .15]])
                repairs.append(shapely.Polygon(c + ring[:, :1] * along + ring[:, 1:] * left))
    return repairs, drains


def surface(positions, indices, materials, profiles):
    """Insert narrow wear bands without changing a single terrain plane."""
    from .surfaces import split_boundaries

    keep = np.ones(len(indices), bool)
    repairs, drains = details(profiles)
    road = np.isin(materials, [mat_id('road'), mat_id('crossing')])
    if repairs and road.any():
        p, ix, ids, _ = split_boundaries(positions, indices, materials, road,
                                       [(g, ASPHALT_REPAIR) for g in repairs])
        indices = np.concatenate([indices[~road], ix])
        materials = np.concatenate([materials[~road], ids])
        positions = p
        keep = np.ones(len(indices), bool)
    bands = [(p['line'].buffer(width, single_sided=True, join_style='mitre', mitre_limit=2), 0)
             for p in profiles for width in (-.35, -.12, .35)]
    p, ix, ids, normals = split_boundaries(positions, indices, materials, keep, bands,
                                          preserve_materials=True)
    colours = np.ones((len(p), 3), np.float32)
    hard = np.isin(ids, [mat_id(name) for name in ('road', 'crossing', 'sidewalk', 'curb',
                    'cobblestone', 'paving_slab')] + [128, 129, ASPHALT_REPAIR])
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
    query = HeightQuery(Mesh(positions, indices[road], materials[road]))
    points, faces, colours = [], [], []

    def quad(xz, lift, rgb):
        y = query(xz)
        if not np.isfinite(y).all():
            return
        start = len(points)
        p = np.c_[xz[:, 0], y + lift, xz[:, 1]]
        points.extend(p)
        ix = np.array([[0, 1, 2], [0, 2, 3]])
        if np.cross(p[1] - p[0], p[2] - p[0])[1] < 0:
            ix = ix[:, [0, 2, 1]]
        faces.extend(ix + start)
        colours.extend([rgb] * 4)

    for centre, along, left in drains:
        def rectangle(a, b, c, d):
            return centre + np.array([[a, c], [b, c], [b, d], [a, d]]) @ np.array([along, left])
        footprint = shapely.Polygon(rectangle(-.25, .25, -.18, .18))
        # Keep every grate complete, within one tile and entirely on the carriageway.
        if not shapely.box(*tile.bbox.as_tuple()).covers(footprint):
            continue
        area = shapely.union_all(footprints[tree.query(footprint, predicate='intersects')])
        if not area.covers(footprint):
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
