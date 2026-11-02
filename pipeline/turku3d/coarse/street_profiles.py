"""Connected measured road, granite kerb and pavement geometry for visuals and collision."""
import numpy as np
import shapely
from turku3d.col.materials import mat_id
from turku3d.ground.mesh import HeightQuery, Mesh
from .walkable import coordinates

REACH = 1.5
RAILS = (-REACH, -.015, .01, .025, .18, REACH)


def regions(profiles):
    """The granite face and cap; shared by physical and visual classification."""
    return [(p["line"].buffer(.18, single_sided=True, join_style="mitre").union(
             p["line"].buffer(-.015, single_sided=True, join_style="mitre")), mat_id("curb"))
            for p in profiles]


def deformation(positions, profiles):
    result = positions.copy()
    support = np.zeros(len(result))
    best = np.full(len(result), np.inf)
    for patch in sorted(profiles, key=lambda p: (p["key"], p["line"].wkb)):
        line = patch["line"]
        distance = shapely.distance(shapely.points(positions[:, [0, 2]]), line)
        ids = np.flatnonzero(distance <= REACH + 1e-8)
        if not len(ids):
            continue
        station, lateral = coordinates(line, positions[ids][:, [0, 2]])
        lo, hi = [np.interp(station, patch["knots"], patch["heights"][:, side]) for side in (0, 1)]
        rise = np.clip((lateral + .015) / .025, 0, 1)
        target = lo + (hi - .01 - lo) * rise
        bevel = np.clip((lateral - .01) / .015, 0, 1)
        target += .01 * bevel
        blend = np.where(lateral < -.015, (lateral + REACH) / (REACH - .015),
                         np.where(lateral > .18, (REACH - lateral) / (REACH - .18), 1))
        # A fit ending inside the tile must meet the unchanged ground without a wall.
        ends = np.minimum(station, line.length - station)
        blend *= np.clip(ends / .5, 0, 1)
        blend = np.clip(blend, 0, 1)
        # Taper incompatible coarse elevations instead of a per-vertex cliff.
        blend *= np.clip((.45 - abs(target - positions[ids, 1])) / .1, 0, 1)
        valid = ((station >= 0) & (station <= line.length)
                 & (distance[ids] < best[ids] - 1e-10) & (blend > 0))
        selected = ids[valid]
        result[selected, 1] = positions[selected, 1] + (target[valid] - positions[selected, 1]) * blend[valid]
        support[selected] = blend[valid]
        best[selected] = distance[selected]
    return result, support


def grade(positions, profiles):
    return deformation(positions, profiles)[0]


def conform(positions, indices, materials):
    """Propagate hanging edge vertices before grading; retain every measured vertex.

    Clipped polygons may omit a neighbour's collinear boundary vertex. A fan
    through the original face centroid enforces all boundary segments without
    Earcut removing those collinear vertices again. Only affected faces expand.
    """
    used = np.unique(indices)
    _, first, inverse = np.unique(np.round(positions[used], 8), axis=0,
                                  return_index=True, return_inverse=True)
    p = positions[used[first]]
    remap = np.empty(len(positions), np.int64)
    remap[used] = inverse
    ix = remap[indices]
    triangles = p[ix]
    valid = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                    triangles[:, 2] - triangles[:, 0]), axis=1) > 1e-12
    ix, materials = ix[valid], materials[valid]
    directed = ix[:, [[0, 1], [1, 2], [2, 0]]]
    edges, edge_ids = np.unique(np.sort(directed.reshape(-1, 2), axis=1),
                                axis=0, return_inverse=True)
    edge_ids = edge_ids.reshape(-1, 3)
    a, b = p[edges[:, 0]], p[edges[:, 1]]
    delta = b[:, [0, 2]] - a[:, [0, 2]]
    length2 = np.sum(delta * delta, axis=1)
    active = np.flatnonzero(length2 > 1e-14)
    lines = shapely.linestrings(p[edges[active]][:, :, [0, 2]])
    ei, vi = shapely.STRtree(shapely.points(p[:, [0, 2]])).query(
        lines, predicate="dwithin", distance=1e-7)
    ei = active[ei]
    t = np.sum((p[vi][:, [0, 2]] - a[ei][:, [0, 2]]) * delta[ei], axis=1) / length2[ei]
    expected_y = a[ei, 1] + t * (b[ei, 1] - a[ei, 1])
    inside = ((t > 1e-7) & (t < 1 - 1e-7) & (abs(p[vi, 1] - expected_y) < 1e-7))
    ei, vi, t = ei[inside], vi[inside], t[inside]
    order = np.lexsort((vi, t, ei))
    splits = {}
    for edge, vertex in zip(ei[order], vi[order], strict=True):
        splits.setdefault(int(edge), []).append(int(vertex))
    affected = np.any(np.isin(edge_ids, list(splits)), axis=1)
    faces = [ix[~affected]]
    ids = [materials[~affected]]
    centres = []
    for face in np.flatnonzero(affected):
        boundary = []
        for side, (start, end) in enumerate(directed[face]):
            boundary.append(int(start))
            extra = splits.get(int(edge_ids[face, side]), [])
            boundary.extend(extra if start < end else extra[::-1])
        centre = len(p) + len(centres)
        centres.append(p[ix[face]].mean(axis=0))
        faces.append(np.c_[boundary, np.roll(boundary, -1), np.full(len(boundary), centre)])
        ids.append(np.full(len(boundary), materials[face], np.uint8))
    if centres:
        p = np.concatenate([p, centres])
    return p, np.concatenate(faces).astype(np.uint32), np.concatenate(ids)


def constrain(mesh, profiles):
    """Insert cross-section rails into the existing dry terrain, preserving its holes."""
    from .surfaces import split_boundaries

    if not profiles:
        return mesh
    bands = [p["line"].buffer(width, single_sided=True, join_style="mitre")
             for p in profiles for width in RAILS]
    cross = []
    for patch in profiles:
        line = patch["line"]
        for station in np.unique(np.r_[patch["knots"], .5, line.length - .5]):
            centre = np.array(line.interpolate(station).coords[0])
            tangent = (np.array(line.interpolate(min(line.length, station + .05)).coords[0])
                       - np.array(line.interpolate(max(0, station - .05)).coords[0]))
            tangent /= max(np.linalg.norm(tangent), 1e-12)
            normal = np.array([-tangent[1], tangent[0]])
            cross.append(shapely.LineString([centre - normal * (REACH + .1),
                                            centre + normal * (REACH + .1)]))
    # Disjoint planar faces retain every rail. Nested buffers alone would let
    # last-region precedence consume inner rails before they get triangulated.
    network = shapely.union_all([*[p.boundary for p in bands], *cross])
    cuts = [(p, 0) for p in shapely.get_parts(shapely.polygonize(shapely.get_parts(network)))]
    p, ix, ids, _ = split_boundaries(mesh.positions, mesh.indices, mesh.material,
                                    np.ones(len(mesh.indices), bool), cuts, preserve_materials=True)
    p, ix, ids = conform(p, ix, ids)
    terrain = np.isin(ids, [mat_id(k) for k in (
        "unknown", "road", "sidewalk", "curb", "curb_dropped", "crossing",
        "cycleway", "cobblestone", "paving_slab", "gravel", "soil", "grass",
    )])
    triangles = p[ix]
    planar = abs(np.cross(triangles[:, 1] - triangles[:, 0],
                           triangles[:, 2] - triangles[:, 0])[:, 1]) > 1e-12
    graded, support = deformation(p, profiles)
    # Nearby water, walls and structure decks keep their surveyed geometry.
    # Zero-area clipping slivers are not structures: protecting their vertices
    # would pin just one copy of a terrain junction and tear open the kerb.
    protected = np.unique(ix[~terrain])
    if len(protected):
        _, groups = np.unique(np.round(p, 8), axis=0, return_inverse=True)
        fixed = np.isin(groups, groups[protected])
        graded[fixed] = p[fixed]
        support[fixed] = 0
    p = graded
    centre = p[ix].mean(axis=1)
    for polygon, material in regions(profiles):
        ids[terrain & planar & (support[ix].mean(axis=1) > .1)
            & shapely.contains_xy(polygon, centre[:, 0], centre[:, 2])] = material
    return Mesh(p, ix, ids).compact()


def material_regions(mesh):
    """Only accepted physical kerb faces may override city finish polygons."""
    faces = mesh.positions[mesh.indices[mesh.material == mat_id("curb")]][:, :, [0, 2]]
    if not len(faces):
        return []
    polygons = shapely.polygons(faces)
    region = shapely.union_all(polygons[shapely.area(polygons) > 1e-12])
    return [(region, mat_id("curb"))] if not region.is_empty else []


def sampler(sample, mesh, profiles):
    if not profiles:
        return sample
    area = shapely.union_all([p.buffer(REACH) for p, _ in material_regions(mesh)])
    shapely.prepare(area)
    query = HeightQuery(mesh)

    def height(x, z):
        if shapely.intersects_xy(area, x, z):
            value = query([[x, z]])[0]
            if np.isfinite(value):
                return float(value)
        return sample(x, z)

    return height
