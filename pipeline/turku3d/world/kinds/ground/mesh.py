"""Compile one ground cell: a height-field skin split by cover, kerbs as closed solids on it, paint as cover,
and bridge decks over water as a second sheet above the skin.

Everything is cell-local here: x east and z south in metres from the cell's north-west corner, heights in N2000.
The skin is one continuous, single-valued sheet, so the cell stays a height field (render-readiness F3): a
carriageway and the pavement beside it are two levels of it, joined by a narrow ramp under the kerb stone, which is
its own closed prism from below the road to the top of the pavement (G1, G2). Paint is not a decal: a stripe is a
region of the skin with the paint cover (F4).

A bridge deck (`decks`) is the one place with two sheets: the triangles inside a deck's outline are drawn at the
deck's level with their own vertices (covers, kerbs and paint as anywhere), and once more at the scan's level as the
bank or the river bed under the deck. A closed slab hangs under the deck's sheet and railings stand on its open
sides. The height raster is single-valued and carries the deck, the level that is walked and driven on.
"""
from __future__ import annotations

import json

import numpy as np
import shapely
from scipy.ndimage import distance_transform_edt, gaussian_filter
from shapely.ops import substring

from ...api import Heightfield, Instances, Material, Part, Output
from ...frame import CELL
from . import decks
from .covers import NAMED

KERB_WIDTH = 0.20        # top of a granite kerb stone
KERB_ROOT = 0.10         # how far the stone goes below the carriageway
KERB_CLEAR = 1.0         # relief closer than this to a kerb line mixes both levels in the scan
KERB_REACH = 2.0         # beyond this distance from a kerb line both levels are the plain relief
GRID = 2.5               # relief resolution of the skin
FAR_STEP = 2.5           # node spacing of the far level of detail
ZEBRA_STRIPE, ZEBRA_PITCH = 0.5, 1.0
DASH_WIDTH = 0.12
NEAR = shapely.box(-0.5, -0.5, CELL + 0.5, CELL + 0.5)   # the cell and a little more, in its own frame
FLUSH = 0.005          # a kerb stone whose top is this near the skin under it is flush: no solid
WATER_LEVEL = 0.0       # N2000: the river and the sea share one datum (the old generator's water level)
BED_DEPTH = 1.0         # how far under the water level the bed lies, until soundings say more
SLIVER = 0.0005        # a triangle lower than this over its longest side is a vertex on an edge
UNDER_CLEAR = 0.3       # m: where the scan's ground is this near the deck's top there is no sheet under the deck
SLAB_COVER = {'family': 'concrete', 'params': {'colour': '#8d8a84', 'finish': 'cast', 'scale_m': 1.2}, 'color': (0.27, 0.25, 0.23), 'roughness': 0.85}
RAILING = 1.2           # m: height of a deck's railing panel from the carriageway level (1.08 m over a footway)
RAILING_PANEL = 2.0     # m: length of a railing panel (the fence kind's iron panel)
RAILING_INSET = 0.12    # m from the deck's edge to the railing's line
RAILING_DROP = 1.0      # m of fall beside the deck from which a railing stands


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
    # A kerb stone lines the carriageway against everything the street register maps beside it and against green
    # (a verge, a lawn, a park): 22 of the 25 carriageways read from the dev points' photos have a kerb, grass
    # verges included. Unmapped land, yards and track beds get none: a street that runs on unmapped has no stone
    # across it.
    kerbed = shapely.unary_union([shape for shape, s in surfaces if s['level'] == 'high'
                                  and (s['source'] in ('street', 'green') or s.get('cover', {}).get('family') in ('grass', 'soil'))])
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


def triangulate(pieces, seams=np.zeros((0, 2)), cuts=()) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One conforming triangulation of all pieces: vertices (n, 2), triangles (m, 3) and the piece of each. `cuts`
    are lines no triangle may cross (a deck's outline).

    The faces of the planar arrangement of the piece boundaries and a regular grid (the relief resolution of the
    skin) are triangulated one by one, so every edge is shared by the faces on both sides of it.
    """
    ticks = np.arange(0.0, CELL + 1e-9, GRID)
    lines = [shapely.LineString([(t, 0.0), (t, CELL)]) for t in ticks] + [shapely.LineString([(0.0, t), (CELL, t)]) for t in ticks]
    # A seam point becomes a node of the cell's edge: a short stub across the edge there, of which only the node stays.
    lines += [shapely.LineString([(x, y), (x + (0.01 if x == 0 else -0.01 if x == CELL else 0.0),
                                           y + (0.0 if x in (0.0, CELL) else 0.01 if y == 0 else -0.01))]) for x, y in seams]
    lines += [piece.boundary for piece, _, _ in pieces]
    lines += list(cuts)
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


def stations(points: np.ndarray, kerb_lines, origin_xz) -> np.ndarray:
    """For each point, how far along its kerb it lies, in metres measured in the world along the direction of the
    nearest kerb line, so the joints between kerb stones fall at the same places in neighbouring cells."""
    runs = [g for g in shapely.get_parts(kerb_lines) if g.geom_type == 'LineString' and g.length > 0]
    if not runs or not len(points):
        return np.zeros(len(points))
    runs = np.array(runs, dtype=object)
    spots = shapely.points(points)
    mine = runs[shapely.STRtree(runs).nearest(spots)]
    at = shapely.line_locate_point(mine, spots)
    length = shapely.length(mine)
    a = shapely.get_coordinates(shapely.line_interpolate_point(mine, np.maximum(at - 0.25, 0.0)))
    b = shapely.get_coordinates(shapely.line_interpolate_point(mine, np.minimum(at + 0.25, length)))
    run = b - a
    run /= np.maximum(np.linalg.norm(run, axis=1, keepdims=True), 1e-9)
    # One sense per direction, whichever way the line happens to be drawn.
    major = np.where(np.abs(run[:, 0]) >= np.abs(run[:, 1]), run[:, 0], run[:, 1])
    run[major < 0] *= -1
    return np.einsum('ij,ij->i', points + origin_xz, run)


def solid(points, triangles, top, bottom, origin_xz, along) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The closed prism over some triangles of the cell (a kerb band, a deck's slab) between a top and a bottom height
    per vertex, with a wall on every rim edge. Returns positions (heights absolute), triangles, normals, uvs and the
    station along the kerb."""
    used, inverse = np.unique(triangles, return_inverse=True)
    tris = inverse.reshape(-1, 3)
    xz = points[used]
    top, bottom = top[used], bottom[used]
    count = len(used)
    positions = [np.column_stack([xz[:, 0], top, xz[:, 1]]), np.column_stack([xz[:, 0], bottom, xz[:, 1]])]
    normals = [np.tile([0.0, 1.0, 0.0], (count, 1)), np.tile([0.0, -1.0, 0.0], (count, 1))]
    world = xz + origin_xz
    uvs = [world, world]
    station = [along[used], along[used]]
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
        station.append(np.concatenate([along[used][a], along[used][b], along[used][b], along[used][a]]))
        n = len(rim)
        k = np.arange(n) + base
        wall = np.concatenate([np.column_stack([k, k + n, k + 2 * n]), np.column_stack([k, k + 2 * n, k + 3 * n])])
        # Orient each wall to its outward normal.
        p = np.concatenate(positions)
        normal = np.cross(p[wall[:, 1]] - p[wall[:, 0]], p[wall[:, 2]] - p[wall[:, 0]])
        flip = np.einsum('ij,ij->i', normal, np.tile(outward, (2, 1))) < 0
        wall[flip] = wall[flip][:, ::-1]
        faces.append(wall)
    return np.concatenate(positions), np.concatenate(faces), np.concatenate(normals), np.concatenate(uvs), np.concatenate(station)


def railings(spans, ground_at, base: float) -> list[Instances]:
    """Railing panels and posts along the sides of the decks where the ground beside the deck lies well under it,
    for the panels whose middle is in this cell. Panels are counted along each deck's whole outline, so the cells a
    deck crosses place the same panels."""
    from ..fence import IRON, iron_panel, post
    from ...objects.common import yaw_of_run

    panels, posts, panel_ids, post_ids = [], [], [], []
    for key, shape, axis, ends in spans:
        for n, ring in enumerate(polygon.exterior for polygon in polygons(shape)):
            count = max(int(ring.length // RAILING_PANEL), 1)
            at = np.arange(count + 1) * (ring.length / count)
            joints = shapely.get_coordinates(shapely.line_interpolate_point(ring, at))
            for k, (a, b) in enumerate(zip(joints[:-1], joints[1:])):
                middle = (a + b) / 2
                run = b - a
                length = float(np.hypot(*run))
                if length < 0.5 or not (0.0 <= middle[0] < CELL and 0.0 <= middle[1] < CELL):
                    continue
                side = np.array([-run[1], run[0]]) / length
                inward = side if shape.contains(shapely.Point(middle + side * RAILING_INSET)) else -side
                outside = middle - inward * 1.0
                top = float(decks.level(axis, ends, middle[:1], middle[1:])[0])
                if top - float(ground_at(outside[:1], outside[1:])[0]) < RAILING_DROP:
                    continue
                yaw = yaw_of_run(float(np.degrees(np.arctan2(run[0], -run[1]))))
                a, b, middle = a + inward * RAILING_INSET, b + inward * RAILING_INSET, middle + inward * RAILING_INSET
                panels.append([middle[0], top - base, middle[1], yaw, length / RAILING_PANEL, 1.0, 1.0])
                panel_ids.append(f'{key}/railing-{n}-{k}')
                posts.append([a[0], top - base, a[1], yaw, 1.0, 1.0, 1.0])
                post_ids.append(f'{key}/railing-post-{n}-{k}')
    if not panels:
        return []
    return [Instances(iron_panel(RAILING, IRON).parts(), np.round(np.array(panels), 4), ids=panel_ids, behaviour='fixed',
                      collision={'shape': 'box', 'size': [RAILING_PANEL, RAILING, 0.06]}),
            Instances(post('iron', RAILING, IRON).parts(), np.round(np.array(posts), 4), ids=post_ids, behaviour='fixed',
                      collision={'shape': 'box', 'size': [0.1, RAILING, 0.1]})]


def compile_cell(description: dict) -> Output:
    i, j = description['cell']
    from ...frame import ORIGIN_E, ORIGIN_N

    e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
    origin_xz = np.array([i * CELL, j * CELL])
    relief = Relief(description['relief'])
    base = float(np.floor(relief.height.min()))
    pieces, low_area, kerb_lines, seams = partition(description, e0, n1)
    low, high = levels(relief, low_area, kerb_lines)
    cell = shapely.box(0, 0, CELL, CELL)
    spans = [(deck['key'], local_shape(deck['rings'], e0, n1), local_line(deck['axis'], e0, n1), deck['ends'])
             for deck in description.get('decks', [])]
    cuts = [part.boundary for _, shape, _, _ in spans for part in polygons(shapely.set_precision(shape.intersection(cell), 0.001))]
    points, triangles, owner = triangulate(pieces, seams, cuts)

    def sampler(raster):
        slope_z, slope_x = np.gradient(raster, relief.step)
        normal = np.dstack([-slope_x, np.ones_like(raster), -slope_z])
        return (lambda x, z: relief.sample(raster, x, z)), (lambda x, z: relief.sample(normal, x, z))

    (low_at, low_normal), (high_at, high_normal) = sampler(low), sampler(high)

    # Skin: one continuous sheet. A vertex that a carriageway triangle uses lies at the carriageway level for every
    # triangle that shares it, so the sheet has no cracks; the strip under a kerb stone ramps between the levels.
    roles = np.array([role for _, _, role in pieces] + ['under'])
    piece_covers = [cover for _, cover, _ in pieces] + [NAMED['water']]
    px, pz = points[:, 0], points[:, 1]
    vertex_low, vertex_high = low_at(px, pz), high_at(px, pz)
    # Decks: the triangles inside a deck's outline get vertices of their own at the deck's level (the carriageway
    # on the line between the abutments, everything else a kerb's height above it; never under the scanned ground,
    # which is the street at the abutments). The same triangles at the scan's level are the sheet under the deck.
    centre = points[triangles].mean(axis=1)
    deck_of = np.full(len(triangles), -1)
    for k, (_, shape, _, _) in enumerate(spans):
        deck_of[shapely.contains_xy(shape, centre[:, 0], centre[:, 1])] = k
    wet = [local_shape(s['rings'], e0, n1) for s in description['surfaces'] if s['level'] == 'bed']
    under = triangles[deck_of >= 0]
    under_bed = shapely.contains_xy(shapely.unary_union(wet), centre[deck_of >= 0, 0], centre[deck_of >= 0, 1]) if wet and len(under) \
        else np.zeros(len(under), bool)
    on_deck = np.zeros(len(points), bool)
    deck_level = np.zeros(len(points))
    for k, (_, _, axis, ends) in enumerate(spans):
        mine = np.flatnonzero(deck_of == k)
        if not len(mine):
            continue
        used, inverse = np.unique(triangles[mine], return_inverse=True)
        top = decks.level(axis, ends, points[used, 0], points[used, 1])
        triangles[mine] = inverse.reshape(-1, 3) + len(points)
        points = np.concatenate([points, points[used]])
        vertex_low = np.concatenate([vertex_low, np.maximum(top, vertex_low[used])])
        vertex_high = np.concatenate([vertex_high, np.maximum(top + decks.KERB, vertex_high[used])])
        on_deck = np.concatenate([on_deck, np.ones(len(used), bool)])
        deck_level = np.concatenate([deck_level, top])
    px, pz = points[:, 0], points[:, 1]
    low_triangle = np.isin(roles[owner], ('low', 'paint'))
    low_vertex = np.zeros(len(points), bool)
    low_vertex[triangles[low_triangle].ravel()] = True
    # Whole millimetres, with ties broken upwards past float noise: a neighbour computes the same border heights.
    heights = np.floor(np.where(low_vertex, vertex_low, vertex_high) * 1000 + 0.5 + 1e-6) / 1000
    # Under open water the skin is the bed: every vertex a water piece uses lies below the water level, so the bank's
    # last triangles fall to it (the quay or bank face until a wall of its own stands there). A deck's own sheet
    # is over the water, the sheet under it is the bed.
    bed_vertex = np.zeros(len(points), bool)
    bed_vertex[triangles[(roles[owner] == 'bed') & (deck_of < 0)].ravel()] = True
    bed_vertex[under[under_bed].ravel()] = True
    heights = np.where(bed_vertex, np.minimum(heights, WATER_LEVEL - BED_DEPTH), heights)
    vertex_normals = np.where(low_vertex[:, None], low_normal(px, pz), high_normal(px, pz))
    vertex_normals /= np.linalg.norm(vertex_normals, axis=1, keepdims=True)
    vertex_normals[on_deck] = (0.0, 1.0, 0.0)
    if len(under):
        # No second sheet where the two would lie in each other (the deck's ends on the abutments).
        clear = (np.where(on_deck, heights, np.inf)[triangles[deck_of >= 0]] - heights[under]).max(axis=1) > UNDER_CLEAR
        triangles = np.concatenate([triangles, under[clear]])
        owner = np.concatenate([owner, np.full(int(clear.sum()), len(pieces))])
        deck_of = np.concatenate([deck_of, np.full(int(clear.sum()), -1)])

    stone_top = np.maximum(vertex_high, vertex_low)
    groups: dict[str, tuple[dict, str, list]] = {}
    kerb_triangles, kerb_cover = [], None
    for index, (cover, role) in enumerate(zip(piece_covers, roles)):
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
    along = stations(points, kerb_lines, origin_xz)
    parts = []
    for key in sorted(groups):
        cover, name, chunks = groups[key]
        used, inverse = np.unique(triangles[np.concatenate(chunks)], return_inverse=True)
        xz = points[used]
        positions = np.column_stack([xz[:, 0], heights[used] - base, xz[:, 1]])
        # Kerb stone carries its station along the kerb, from which the family draws the joints between stones.
        extra = {'along': along[used].astype(np.float32)} if cover['family'] == 'kerbstone' else {}
        parts.append(Part(name, positions, facing_up(xz, inverse.reshape(-1, 3)), material_of(cover),
                          normals=vertex_normals[used], uvs=xz + origin_xz, attributes=extra))
    if kerb_triangles and sum(len(chunk) for chunk in kerb_triangles):
        positions, faces, normals, uvs, station = solid(points, np.concatenate(kerb_triangles), stone_top, vertex_low - KERB_ROOT, origin_xz, along)
        positions[:, 1] -= base
        extra = {'along': station.astype(np.float32)} if kerb_cover['family'] == 'kerbstone' else {}
        parts.append(Part('kerbs', positions, faces, material_of(kerb_cover), normals=normals, uvs=uvs, attributes=extra))
    # The slab of each deck: a closed solid from just under the deck's sheet to the underside.
    slabs = [triangles[deck_of == k] for k in range(len(spans)) if (deck_of == k).any()]
    if slabs:
        positions, faces, normals, uvs, _ = solid(points, np.concatenate(slabs), heights - 0.02, deck_level - decks.SLAB, origin_xz, along)
        positions[:, 1] -= base
        parts.append(Part('deck-slab', positions, faces, material_of(SLAB_COVER), normals=normals, uvs=uvs))

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
    far_heights = relief.sample(plain, fx, fz)
    far_heights = np.where(roles[nearest] == 'bed', np.minimum(far_heights, WATER_LEVEL - BED_DEPTH), far_heights)
    for _, shape, axis, ends in spans:
        inside = shapely.contains_xy(shape, fx, fz)
        far_heights = np.where(inside, np.maximum(far_heights, decks.level(axis, ends, fx, fz)), far_heights)
    parts.append(Part('far', np.column_stack([fx, far_heights - base, fz]), facing_up(far_xz, far),
                      Material('ground_far', {}, (1.0, 1.0, 1.0), 0.95), normals=far_normals, uvs=far_xz + origin_xz,
                      colors=colors, lod=1))
    # The height field the skin was cut from: the carriageway level inside carriageways, the other level elsewhere.
    side = int(round(CELL / relief.step)) + 1
    inner = slice(relief.halo, relief.halo + side)
    inside_low = shapely.contains_xy(low_area, relief.x[inner, inner].ravel(), relief.z[inner, inner].ravel()).reshape(side, side)
    raster = np.where(inside_low, low[inner, inner], high[inner, inner])
    beds = [piece for piece, _, role in pieces if role == 'bed']
    if beds:
        inside_bed = shapely.contains_xy(shapely.unary_union(beds), relief.x[inner, inner].ravel(), relief.z[inner, inner].ravel()).reshape(side, side)
        raster = np.where(inside_bed, np.minimum(raster, WATER_LEVEL - BED_DEPTH), raster)
    rx, rz = relief.x[inner, inner].ravel(), relief.z[inner, inner].ravel()
    for _, shape, axis, ends in spans:
        inside = shapely.contains_xy(shape.buffer(decks.APRON), rx, rz).reshape(side, side)
        if inside.any():
            top = decks.level(axis, ends, rx, rz).reshape(side, side) + np.where(inside_low, 0.0, decks.KERB)
            raster = np.where(inside, np.maximum(raster, top), raster)
    field = Heightfield((i * CELL, j * CELL), relief.step, raster)
    rails = railings(spans, lambda x, z: relief.sample(relief.height, x, z), base)
    return Output((i * CELL, base, j * CELL), parts, instances=rails, heightfield=field, error_m={0: 0.01, 1: 0.15})
