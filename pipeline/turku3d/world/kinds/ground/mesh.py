"""Compile one ground cell: a height-field skin split by cover, kerbs as closed solids on it, paint as cover.

Everything is cell-local here: x east and z south in metres from the cell's north-west corner, heights in N2000.
The skin is one continuous, single-valued sheet, so the cell stays a height field (render-readiness F3): a
carriageway and the pavement beside it are two levels of it, joined by a narrow ramp under the kerb stone, which is
its own closed prism from below the road to the top of the pavement (G1, G2). Paint is not a decal: a stripe is a
region of the skin with the paint cover (F4).
"""
from __future__ import annotations

import json

import numpy as np
import shapely
from scipy.ndimage import distance_transform_edt, gaussian_filter
from shapely.ops import substring

from ...api import Heightfield, Material, Part, Output
from ...frame import CELL
from .covers import NAMED

KERB_WIDTH = 0.16        # top of a granite kerb stone
KERB_ROOT = 0.10         # how far the stone goes below the carriageway
KERB_CLEAR = 1.0         # relief closer than this to a kerb line mixes both levels in the scan
KERB_REACH = 2.0         # beyond this distance from a kerb line both levels are the plain relief
GRID = 2.5               # relief resolution of the skin
FAR_STEP = 2.5           # node spacing of the far level of detail
ZEBRA_STRIPE, ZEBRA_PITCH = 0.5, 1.0
DASH_WIDTH = 0.12
NEAR = shapely.box(-0.5, -0.5, CELL + 0.5, CELL + 0.5)   # the cell and a little more, in its own frame
FLUSH = 0.005          # a kerb stone whose top is this near the skin under it is flush: no solid
SLIVER = 0.0005        # a triangle lower than this over its longest side is a vertex on an edge


def polygons(shape) -> list:
    return [p for p in shapely.get_parts(shape) if p.geom_type == 'Polygon' and p.area > 1e-4]


def local_shape(rings, e0: float, n1: float):
    rings = [np.column_stack([np.asarray(r)[:, 0] - e0, n1 - np.asarray(r)[:, 1]]) for r in rings]
    return shapely.make_valid(shapely.Polygon(rings[0], rings[1:]))


def local_line(points, e0: float, n1: float):
    points = np.asarray(points, float)
    return shapely.LineString(np.column_stack([points[:, 0] - e0, n1 - points[:, 1]]))


class Relief:
    """The cell's height nodes with a halo, and bilinear sampling of any raster on that lattice."""

    def __init__(self, relief: dict):
        self.step, self.halo, self.side = relief['step'], relief['halo'], relief['side']
        self.height = np.asarray(relief['heights_mm'], np.float64).reshape(self.side, self.side) / 1000.0
        axis = (np.arange(self.side) - self.halo) * self.step
        self.x, self.z = np.meshgrid(axis, axis)   # row = z (south), column = x (east)

    def sample(self, raster: np.ndarray, x, z) -> np.ndarray:
        u = np.asarray(x) / self.step + self.halo
        v = np.asarray(z) / self.step + self.halo
        c = np.clip(np.floor(u).astype(int), 0, self.side - 2)
        r = np.clip(np.floor(v).astype(int), 0, self.side - 2)
        fu, fv = np.clip(u - c, 0, 1), np.clip(v - r, 0, 1)
        if raster.ndim == 3:
            fu, fv = fu[:, None], fv[:, None]
        return (raster[r, c] * (1 - fu) * (1 - fv) + raster[r, c + 1] * fu * (1 - fv)
                + raster[r + 1, c] * (1 - fu) * fv + raster[r + 1, c + 1] * fu * fv)


def levels(relief: Relief, low_area, kerb_lines) -> tuple[np.ndarray, np.ndarray]:
    """The carriageway level and the level of everything else, as rasters on the relief lattice.

    A 0.5 m scan smears a kerb's step over a metre. Each level is therefore read only from nodes clear of the kerb
    on its own side and carried to the kerb line from the nearest of them; away from kerbs both are the relief.
    """
    plain = gaussian_filter(relief.height, 0.6, mode='nearest')
    if kerb_lines.is_empty:
        return plain, plain
    x, z = relief.x.ravel(), relief.z.ravel()
    shape = relief.height.shape

    def carried(clear: np.ndarray) -> np.ndarray:
        if not clear.any():
            return plain
        index = distance_transform_edt(~clear, return_distances=False, return_indices=True)
        return gaussian_filter(relief.height[index[0], index[1]], 0.6, mode='nearest')

    low = carried(shapely.contains_xy(low_area.buffer(-KERB_CLEAR), x, z).reshape(shape))
    high = carried(~shapely.contains_xy(low_area.buffer(KERB_CLEAR), x, z).reshape(shape))
    distance = shapely.distance(shapely.points(x, z), kerb_lines).reshape(shape)
    near = np.clip((KERB_REACH - distance) / (KERB_REACH - KERB_CLEAR), 0.0, 1.0)
    near = near * near * (3 - 2 * near)
    low = plain + near * (low - plain)
    high = plain + near * (high - plain)
    return low, np.maximum(high, low)


def zebra(line, width: float, low_area) -> list:
    """The stripes of a crossing: bars along the carriageway, half a metre on and half off along the walking line."""
    start, end = np.asarray(line.coords[0]), np.asarray(line.coords[-1])
    length = float(np.linalg.norm(end - start))
    if length < 1.0:
        return []
    along = (end - start) / length
    across = np.array([-along[1], along[0]])
    bars = []
    for station in np.arange(-2.0 + ZEBRA_PITCH / 4, length + 2.0, ZEBRA_PITCH):
        a, b = start + along * station, start + along * (station + ZEBRA_STRIPE)
        bar = shapely.Polygon([a - across * width / 2, b - across * width / 2, b + across * width / 2, a + across * width / 2])
        # Whole bars only, clear of the kerb, as painted.
        if low_area.contains(bar.buffer(0.12)):
            bars.append(bar)
    return bars


def dashes(line, start: float, dash: float, gap: float, low_area) -> list:
    """Dashes of a lane line. `start` is the station of the line's first point on its whole road, so the rhythm
    continues across cells."""
    period = dash + gap
    first = np.ceil((start - dash) / period) * period - start
    bars = []
    for station in np.arange(first, line.length, period):
        a, b = max(station, 0.0), min(station + dash, line.length)
        if b - a < 0.3:
            continue
        bar = substring(line, a, b).buffer(DASH_WIDTH / 2, cap_style='flat')
        # Judged near the cell only: the line and the carriageway both end at the halo, where a neighbour sees more.
        if bar.intersection(NEAR).difference(low_area).area < 1e-4:
            bars.append(bar)
    return bars


def areal(shape):
    """The polygons of a shape as one geometry (an overlay of mixed collections has no answer)."""
    if shape.geom_type in ('Polygon', 'MultiPolygon'):
        return shape
    return shapely.unary_union(polygons(shape) or [shapely.Polygon()])


def remainder(shape, taken):
    """`shape` without `taken`; where the overlay fails on a degenerate outline, both are first snapped to the
    millimetre lattice."""
    shape, taken = areal(shape), areal(taken)
    try:
        return shape.difference(taken)
    except shapely.errors.GEOSException:
        shape, taken = (areal(shapely.make_valid(shapely.set_precision(g, 0.001))) for g in (shape, taken))
        return shape.difference(taken)


def partition(description: dict, e0: float, n1: float):
    """The cell as disjoint pieces [(shape, cover, role)], the carriageway area and the kerb lines."""
    cell = shapely.box(0, 0, CELL, CELL)
    surfaces = [(local_shape(s['rings'], e0, n1), s) for s in sorted(description['surfaces'], key=lambda s: s['rank'])]
    low_area = shapely.unary_union([shape for shape, s in surfaces if s['level'] == 'low']) if surfaces else shapely.Polygon()
    kerbed = shapely.unary_union([shape for shape, s in surfaces if s['level'] == 'high' and s['source'] == 'street'])
    if low_area.is_empty or kerbed.is_empty:
        kerb_lines, band = shapely.LineString(), shapely.Polygon()
    else:
        kerb_lines = low_area.boundary.intersection(kerbed.buffer(0.03))
        kerb_lines = shapely.unary_union([g for g in shapely.get_parts(kerb_lines) if g.geom_type == 'LineString' and g.length > 0.2] or [shapely.LineString()])
        band = low_area.buffer(KERB_WIDTH, join_style='mitre').intersection(kerbed).difference(low_area)

    layers = []
    paint = []
    for crossing in description.get('crossings', []):
        paint += zebra(local_line(crossing['line'], e0, n1), crossing['width'], low_area)
    zebra_zone = shapely.unary_union(paint).buffer(1.0) if paint else shapely.Polygon()
    for lane in description.get('lanes', []):
        bars = dashes(local_line(lane['line'], e0, n1), lane['start'], lane['dash'], lane['gap'], low_area)
        paint += [bar for bar in bars if not bar.intersects(zebra_zone)]
    if paint:
        layers.append((shapely.unary_union(paint), NAMED['paint'], 'paint'))
    if not band.is_empty:
        layers.append((band, description.get('kerb', NAMED['kerb']), 'kerb'))
    layers += [(shape, s['cover'], s['level']) for shape, s in surfaces]

    # The same overlay once more on the strip just outside the cell: where a piece of a neighbour ends on the shared
    # edge, this cell needs a vertex there too (`seams`), or the two skins would not meet point for point.
    rim = NEAR.difference(cell)
    pieces, taken, beyond, seams = [], shapely.Polygon(), shapely.Polygon(), []
    for shape, cover, role in layers:
        rest = shapely.set_precision(remainder(shape.intersection(cell), taken), 0.001)
        for part in polygons(rest):
            pieces.append((part, cover, role))
        if not rest.is_empty:
            taken = areal(shapely.unary_union([taken, areal(rest)]))
        outer = shapely.set_precision(remainder(shape.intersection(rim), beyond), 0.001)
        if not outer.is_empty:
            seams.append(shapely.get_coordinates(outer))
            beyond = areal(shapely.unary_union([beyond, areal(outer)]))
    for part in polygons(shapely.set_precision(cell.difference(taken), 0.001)):
        pieces.append((part, description['base'], 'high'))
    seams = np.concatenate(seams) if seams else np.zeros((0, 2))
    on_edge = (np.isin(seams, (0.0, CELL)).any(axis=1)) & (seams >= 0).all(axis=1) & (seams <= CELL).all(axis=1)
    return pieces, low_area, kerb_lines, np.unique(seams[on_edge], axis=0)


def triangulate(pieces, seams=np.zeros((0, 2))) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One conforming triangulation of all pieces: vertices (n, 2), triangles (m, 3) and the piece of each.

    The faces of the planar arrangement of the piece boundaries and a regular grid (the relief resolution of the
    skin) are triangulated one by one, so every edge is shared by the faces on both sides of it.
    """
    ticks = np.arange(0.0, CELL + 1e-9, GRID)
    lines = [shapely.LineString([(t, 0.0), (t, CELL)]) for t in ticks] + [shapely.LineString([(0.0, t), (CELL, t)]) for t in ticks]
    # A seam point becomes a node of the cell's edge: a short stub across the edge there, of which only the node stays.
    lines += [shapely.LineString([(x, y), (x + (0.01 if x == 0 else -0.01 if x == CELL else 0.0),
                                           y + (0.0 if x in (0.0, CELL) else 0.01 if y == 0 else -0.01))]) for x, y in seams]
    lines += [piece.boundary for piece, _, _ in pieces]
    noded = shapely.unary_union(lines)   # the pieces are on a millimetre lattice already; snapping again opens rings
    faces = shapely.get_parts(shapely.polygonize(list(shapely.get_parts(noded))))
    faces = faces[shapely.area(faces) > 0]
    inside = shapely.point_on_surface(faces)
    tree = shapely.STRtree([piece for piece, _, _ in pieces])
    owner = np.full(len(faces), -1)
    hit_face, hit_piece = tree.query(inside, predicate='within')
    owner[hit_face] = hit_piece
    lost = np.flatnonzero(owner < 0)
    if len(lost):
        keep = shapely.contains_xy(shapely.box(0, 0, CELL, CELL), shapely.get_x(inside[lost]), shapely.get_y(inside[lost]))
        owner[lost[keep]] = tree.nearest(inside[lost[keep]])
    # Constrained Delaunay keeps every vertex of a face's ring, also one that lies on a straight edge because the
    # face beyond needs it; an ear-clipper drops those and leaves T-junctions.
    kept = owner >= 0
    cut = shapely.constrained_delaunay_triangles(faces[kept])
    corners = shapely.get_coordinates(shapely.get_exterior_ring(shapely.get_parts(cut))).reshape(-1, 4, 2)[:, :3]
    owners = np.repeat(owner[kept], shapely.get_num_geometries(cut))
    points, index = np.unique(np.round(corners.reshape(-1, 2), 3), axis=0, return_inverse=True)
    triangles = index.reshape(-1, 3)
    solid = (triangles[:, 0] != triangles[:, 1]) & (triangles[:, 1] != triangles[:, 2]) & (triangles[:, 0] != triangles[:, 2])
    triangles, owners = without_slivers(points, triangles[solid], owners[solid])
    return points, triangles, owners


def without_slivers(points: np.ndarray, triangles: np.ndarray, owner: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The triangulation without triangles flatter than SLIVER: each one's apex lies on its longest edge, so the
    triangle beyond that edge is split at the apex and the flat one goes. The sheet stays closed, with no vertex
    left on the side of a neighbour's edge."""
    for _ in range(6):
        corner = points[triangles]
        sides = np.linalg.norm(corner[:, [1, 2, 0]] - corner[:, [2, 0, 1]], axis=2)   # the side opposite each corner
        u, v = corner[:, 1] - corner[:, 0], corner[:, 2] - corner[:, 0]
        double_area = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0])
        flat = np.flatnonzero(double_area < SLIVER * sides.max(axis=1))
        if not len(flat):
            break
        across: dict[tuple[int, int], list[int]] = {}
        for number, (a, b, c) in enumerate(triangles.tolist()):
            for edge in ((a, b), (b, c), (c, a)):
                across.setdefault((min(edge), max(edge)), []).append(number)
        gone, added, added_owner = set(), [], []
        flat_set = set(flat.tolist())
        for number in flat.tolist():
            apex = int(np.argmax(sides[number]))
            m, a, b = (int(triangles[number][(apex + turn) % 3]) for turn in range(3))
            beyond = [other for other in across[(min(a, b), max(a, b))] if other != number]
            if beyond and (beyond[0] in gone or beyond[0] in flat_set):
                continue   # its neighbour changes in this round: the next round sees the result
            gone.add(number)
            if beyond:
                c = next(v for v in triangles[beyond[0]].tolist() if v not in (a, b))
                gone.add(beyond[0])
                added += [(a, m, c), (m, b, c)]
                added_owner += [owner[beyond[0]]] * 2
        if not gone:
            break
        keep = np.ones(len(triangles), bool)
        keep[list(gone)] = False
        triangles = np.concatenate([triangles[keep], np.array(added, dtype=triangles.dtype).reshape(-1, 3)])
        owner = np.concatenate([owner[keep], np.array(added_owner, dtype=owner.dtype)])
    return triangles, owner


def facing_up(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Triangles ordered counter-clockwise seen from above in engine axes (z south)."""
    a, b, c = points[triangles[:, 0]], points[triangles[:, 1]], points[triangles[:, 2]]
    turn = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    out = triangles.copy()
    out[turn > 0] = out[turn > 0][:, ::-1]
    return out


def material_of(cover: dict) -> Material:
    return Material(cover['family'], cover.get('params', {}), tuple(cover['color']), cover.get('roughness', 0.9))


def kerb_solid(points, triangles, low_at, high_at, origin_xz) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The closed prism over the kerb band: top at the pavement level, bottom below the carriageway, a wall on every
    rim edge. Returns positions (heights absolute), triangles, normals and uvs."""
    used, inverse = np.unique(triangles, return_inverse=True)
    tris = inverse.reshape(-1, 3)
    xz = points[used]
    low = low_at(xz[:, 0], xz[:, 1])
    top = np.maximum(high_at(xz[:, 0], xz[:, 1]), low)
    bottom = low - KERB_ROOT
    count = len(used)
    positions = [np.column_stack([xz[:, 0], top, xz[:, 1]]), np.column_stack([xz[:, 0], bottom, xz[:, 1]])]
    normals = [np.tile([0.0, 1.0, 0.0], (count, 1)), np.tile([0.0, -1.0, 0.0], (count, 1))]
    world = xz + origin_xz
    uvs = [world, world]
    up = facing_up(xz, tris)
    faces = [up, up[:, ::-1] + count]

    # Rim: directed edges of the band's triangles that no other band triangle shares.
    edges = np.concatenate([up[:, [0, 1]], up[:, [1, 2]], up[:, [2, 0]]])
    keys = np.sort(edges, axis=1)
    _, first, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
    rim = edges[first[counts == 1]]
    if len(rim):
        a, b = rim[:, 0], rim[:, 1]
        run = xz[b] - xz[a]
        length = np.linalg.norm(run, axis=1, keepdims=True)
        run = run / np.maximum(length, 1e-9)
        # `up` winds counter-clockwise seen from above, so the outside of the band is to the right of a -> b.
        outward = np.column_stack([-run[:, 1], np.zeros(len(rim)), run[:, 0]])
        along_a = np.einsum('ij,ij->i', world[a], run)
        along_b = np.einsum('ij,ij->i', world[b], run)
        base = 2 * count
        quad = np.concatenate([
            np.column_stack([xz[a, 0], top[a], xz[a, 1]]), np.column_stack([xz[b, 0], top[b], xz[b, 1]]),
            np.column_stack([xz[b, 0], bottom[b], xz[b, 1]]), np.column_stack([xz[a, 0], bottom[a], xz[a, 1]])])
        positions.append(quad)
        normals.append(np.tile(outward, (4, 1)))
        uvs.append(np.concatenate([np.column_stack([along_a, top[a]]), np.column_stack([along_b, top[b]]),
                                   np.column_stack([along_b, bottom[b]]), np.column_stack([along_a, bottom[a]])]))
        n = len(rim)
        k = np.arange(n) + base
        wall = np.concatenate([np.column_stack([k, k + n, k + 2 * n]), np.column_stack([k, k + 2 * n, k + 3 * n])])
        # Orient each wall to its outward normal.
        p = np.concatenate(positions)
        normal = np.cross(p[wall[:, 1]] - p[wall[:, 0]], p[wall[:, 2]] - p[wall[:, 0]])
        flip = np.einsum('ij,ij->i', normal, np.tile(outward, (2, 1))) < 0
        wall[flip] = wall[flip][:, ::-1]
        faces.append(wall)
    return np.concatenate(positions), np.concatenate(faces), np.concatenate(normals), np.concatenate(uvs)


def compile_cell(description: dict) -> Output:
    i, j = description['cell']
    from ...frame import ORIGIN_E, ORIGIN_N

    e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
    origin_xz = np.array([i * CELL, j * CELL])
    relief = Relief(description['relief'])
    base = float(np.floor(relief.height.min()))
    pieces, low_area, kerb_lines, seams = partition(description, e0, n1)
    low, high = levels(relief, low_area, kerb_lines)
    points, triangles, owner = triangulate(pieces, seams)

    def sampler(raster):
        slope_z, slope_x = np.gradient(raster, relief.step)
        normal = np.dstack([-slope_x, np.ones_like(raster), -slope_z])
        return (lambda x, z: relief.sample(raster, x, z)), (lambda x, z: relief.sample(normal, x, z))

    (low_at, low_normal), (high_at, high_normal) = sampler(low), sampler(high)

    # Skin: one continuous sheet. A vertex that a carriageway triangle uses lies at the carriageway level for every
    # triangle that shares it, so the sheet has no cracks; the strip under a kerb stone ramps between the levels.
    roles = np.array([role for _, _, role in pieces])
    low_triangle = np.isin(roles[owner], ('low', 'paint'))
    low_vertex = np.zeros(len(points), bool)
    low_vertex[triangles[low_triangle].ravel()] = True
    px, pz = points[:, 0], points[:, 1]
    # Whole millimetres, with ties broken upwards past float noise: a neighbour computes the same border heights.
    heights = np.floor(np.where(low_vertex, low_at(px, pz), high_at(px, pz)) * 1000 + 0.5 + 1e-6) / 1000
    vertex_normals = np.where(low_vertex[:, None], low_normal(px, pz), high_normal(px, pz))
    vertex_normals /= np.linalg.norm(vertex_normals, axis=1, keepdims=True)

    stone_top = np.maximum(high_at(px, pz), low_at(px, pz))
    groups: dict[str, tuple[dict, str, list]] = {}
    kerb_triangles, kerb_cover = [], None
    for index, (_, cover, role) in enumerate(pieces):
        mine = np.flatnonzero(owner == index)
        if not len(mine):
            continue
        name = cover['family']
        if role == 'kerb':
            # A stone stands where its top is above the skin under it; a flush stone is the skin itself.
            raised = (stone_top[triangles[mine]] - heights[triangles[mine]]).max(axis=1) > FLUSH
            kerb_triangles.append(triangles[mine][raised])
            kerb_cover, name = cover, 'kerb-bed'
        elif role in ('low', 'paint'):
            name = f"{cover['family']}-carriageway"
        key = json.dumps([cover['family'], cover.get('params', {}), name], sort_keys=True)
        groups.setdefault(key, (cover, name, []))[2].append(mine)
    parts = []
    for key in sorted(groups):
        cover, name, chunks = groups[key]
        used, inverse = np.unique(triangles[np.concatenate(chunks)], return_inverse=True)
        xz = points[used]
        positions = np.column_stack([xz[:, 0], heights[used] - base, xz[:, 1]])
        parts.append(Part(name, positions, facing_up(xz, inverse.reshape(-1, 3)), material_of(cover),
                          normals=vertex_normals[used], uvs=xz + origin_xz))
    if kerb_triangles and sum(len(chunk) for chunk in kerb_triangles):
        positions, faces, normals, uvs = kerb_solid(points, np.concatenate(kerb_triangles), low_at, high_at, origin_xz)
        positions[:, 1] -= base
        parts.append(Part('kerbs', positions, faces, material_of(kerb_cover), normals=normals, uvs=uvs))

    # Far level of detail: the plain height field, coloured per node by the cover under it.
    nodes = int(CELL / FAR_STEP) + 1
    axis = np.arange(nodes) * FAR_STEP
    fx, fz = (a.ravel() for a in np.meshgrid(axis, axis))
    plain = gaussian_filter(relief.height, 0.6, mode='nearest')
    tree = shapely.STRtree([piece for piece, _, _ in pieces])
    nearest = tree.nearest(shapely.points(np.clip(fx, 0.01, CELL - 0.01), np.clip(fz, 0.01, CELL - 0.01)))
    colors = np.array([pieces[k][1]['color'] for k in nearest])
    r, c = np.meshgrid(np.arange(nodes - 1), np.arange(nodes - 1), indexing='ij')
    a = (r * nodes + c).ravel()
    far = np.concatenate([np.column_stack([a, a + nodes, a + 1]), np.column_stack([a + 1, a + nodes, a + nodes + 1])])
    far_xz = np.column_stack([fx, fz])
    far_normals = sampler(plain)[1](fx, fz)
    far_normals /= np.linalg.norm(far_normals, axis=1, keepdims=True)
    parts.append(Part('far', np.column_stack([fx, relief.sample(plain, fx, fz) - base, fz]), facing_up(far_xz, far),
                      Material('ground_far', {}, (1.0, 1.0, 1.0), 0.95), normals=far_normals, uvs=far_xz + origin_xz,
                      colors=colors, lod=1))
    # The height field the skin was cut from: the carriageway level inside carriageways, the other level elsewhere.
    side = int(round(CELL / relief.step)) + 1
    inner = slice(relief.halo, relief.halo + side)
    inside_low = shapely.contains_xy(low_area, relief.x[inner, inner].ravel(), relief.z[inner, inner].ravel()).reshape(side, side)
    field = Heightfield((i * CELL, j * CELL), relief.step, np.where(inside_low, low[inner, inner], high[inner, inner]))
    return Output((i * CELL, base, j * CELL), parts, heightfield=field, error_m={0: 0.01, 1: 0.15})
