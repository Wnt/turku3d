"""Triangle soup grouped by part and material, with builders for the shapes facades are made of.

Local wall coordinates are (u, v, d): u along the wall, v up, d out of the wall plane, in metres; a triangle is
counter-clockwise seen from +d. `Soup.place` maps a local soup into survey coordinates through a facade frame.
"""

import mapbox_earcut
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient


class Soup:
    """Vertices per (part, material key): flat-shaded faces with metric UVs and optional per-vertex extras."""

    def __init__(self):
        self.groups = {}

    def _group(self, part: str, material: str):
        return self.groups.setdefault(
            (part, material), {"p": [], "n": [], "uv": [], "x": [], "t": [], "count": 0}
        )

    def add(self, part, material, positions, triangles, uvs, normals=None, extra=None):
        positions = np.asarray(positions, float).reshape(-1, 3)
        triangles = np.asarray(triangles, np.int64).reshape(-1, 3)
        if len(triangles) == 0:
            return
        if normals is None:
            normals = vertex_normals(positions, triangles)
        group = self._group(part, material)
        group["p"].append(positions)
        group["n"].append(np.asarray(normals, float).reshape(-1, 3))
        group["uv"].append(np.asarray(uvs, float).reshape(-1, 2))
        group["x"].append(
            None
            if extra is None
            else np.broadcast_to(np.asarray(extra, float), (len(positions), 4))
        )
        group["t"].append(triangles + group["count"])
        group["count"] += len(positions)

    def extend(self, other: "Soup"):
        for (part, material), group in other.groups.items():
            for p, n, uv, x, t in zip(
                group["p"], group["n"], group["uv"], group["x"], group["t"], strict=True
            ):
                self.add(part, material, p, t - t.min() if len(t) else t, uv, n, x)

    def place(self, local: "Soup", frame, u0=0.0, v0=0.0, d0=0.0, extra=None, extra_part="glass"):
        """Add a local (u, v, d) soup at wall position (u0, v0, d0) of a frame; `extra` goes to one part's vertices."""
        basis = np.stack([frame.u_dir, np.array([0.0, 0.0, 1.0]), frame.normal])
        shift = np.array([u0, v0, d0])
        for (part, material), group in local.groups.items():
            if not group["p"]:
                continue
            world = frame.origin + (np.vstack(group["p"]) + shift) @ basis
            normals = np.vstack(group["n"]) @ basis
            x = None
            if extra is not None and part == extra_part:
                x = extra
            elif any(e is not None for e in group["x"]):
                x = np.vstack(
                    [
                        e if e is not None else np.zeros((len(q), 4))
                        for e, q in zip(group["x"], group["p"], strict=True)
                    ]
                )
            # Glass keeps its opening-local UVs: the pane's interior is drawn across the opening, not along the wall.
            uv = np.vstack(group["uv"]) + (0.0 if part == extra_part else shift[:2])
            self.add(part, material, world, np.vstack(group["t"]), uv, normals, x)

    def arrays(self):
        """(part, material, positions, normals, uvs, extra or None, triangles) per group, concatenated."""
        for (part, material), group in self.groups.items():
            if not group["p"]:
                continue
            extra = None
            if any(e is not None for e in group["x"]):
                extra = np.vstack(
                    [
                        e if e is not None else np.zeros((len(q), 4))
                        for e, q in zip(group["x"], group["p"], strict=True)
                    ]
                )
            yield (
                part,
                material,
                np.vstack(group["p"]),
                np.vstack(group["n"]),
                np.vstack(group["uv"]),
                extra,
                np.vstack(group["t"]),
            )

    def triangle_count(self) -> int:
        return sum(len(t) for g in self.groups.values() for t in g["t"])


def vertex_normals(positions, triangles):
    """Flat normals for faces whose vertices are not shared between differently facing faces."""
    a, b, c = positions[triangles[:, 0]], positions[triangles[:, 1]], positions[triangles[:, 2]]
    face = np.cross(b - a, c - a)
    normals = np.zeros_like(positions)
    for k in range(3):
        np.add.at(normals, triangles[:, k], face)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.where(length > 1e-12, length, 1.0)


def polygons_of(shape) -> list:
    if shape is None or shape.is_empty:
        return []
    if isinstance(shape, Polygon):
        return [shape]
    if isinstance(shape, MultiPolygon):
        return list(shape.geoms)
    return [g for g in getattr(shape, "geoms", []) if isinstance(g, Polygon)]


def triangulate(polygon: Polygon):
    """(vertices (n, 2), triangles (m, 3)) of a polygon with holes, counter-clockwise."""
    polygon = orient(polygon, 1.0)
    rings = [np.asarray(polygon.exterior.coords)[:-1]]
    rings += [np.asarray(r.coords)[:-1] for r in polygon.interiors]
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return np.zeros((0, 2)), np.zeros((0, 3), np.int64)
    vertices = np.vstack(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    triangles = mapbox_earcut.triangulate_float64(vertices, ends).reshape(-1, 3).astype(np.int64)
    return vertices, triangles


def face(soup: Soup, part, material, shape, d=0.0, uv_origin=(0.0, 0.0), flip=False, extra=None):
    """A flat shape in the wall plane at depth d, facing +d (or -d when flipped)."""
    for polygon in polygons_of(shape):
        if polygon.area < 1e-6:
            continue
        vertices, triangles = triangulate(polygon)
        if len(triangles) == 0:
            continue
        positions = np.column_stack([vertices, np.full(len(vertices), d)])
        normal = np.array([0.0, 0.0, -1.0 if flip else 1.0])
        soup.add(
            part,
            material,
            positions,
            triangles[:, ::-1] if flip else triangles,
            vertices + np.asarray(uv_origin),
            np.tile(normal, (len(vertices), 1)),
            extra,
        )


def ring_walls(soup: Soup, part, material, ring, d0, d1, outward=True, uv_origin=(0.0, 0.0)):
    """Side faces along a closed counter-clockwise ring between depths d0 and d1.

    outward: the faces look away from the ring's inside (the sides of a boss). Otherwise they look into it (the
    reveal of a hole). UVs run along the ring and across the depth.
    """
    ring = np.asarray(ring, float)
    if len(ring) < 3 or abs(d1 - d0) < 1e-6:
        return
    nxt = np.roll(ring, -1, axis=0)
    edge = nxt - ring
    length = np.linalg.norm(edge, axis=1)
    keep = length > 1e-6
    ring, nxt, edge, length = ring[keep], nxt[keep], edge[keep], length[keep]
    lo, hi = min(d0, d1), max(d0, d1)
    n2 = np.column_stack([edge[:, 1], -edge[:, 0]]) / length[:, None]
    if not outward:
        n2 = -n2
    count = len(ring)
    positions = np.empty((count, 4, 3))
    positions[:, 0] = np.column_stack([ring, np.full(count, lo)])
    positions[:, 1] = np.column_stack([nxt, np.full(count, lo)])
    positions[:, 2] = np.column_stack([nxt, np.full(count, hi)])
    positions[:, 3] = np.column_stack([ring, np.full(count, hi)])
    # The wall's texture continues round the corner: the depth runs across the edge's own direction.
    uvs = np.empty((count, 4, 2))
    horizontal = np.abs(edge[:, 0]) >= np.abs(edge[:, 1])
    for k, (corner, depth) in enumerate(((ring, lo), (nxt, lo), (nxt, hi), (ring, hi))):
        uvs[:, k, 0] = corner[:, 0] + uv_origin[0] + np.where(horizontal, 0.0, depth)
        uvs[:, k, 1] = corner[:, 1] + uv_origin[1] + np.where(horizontal, depth, 0.0)
    normals = np.repeat(np.column_stack([n2, np.zeros(count)])[:, None, :], 4, axis=1)
    base = (np.arange(count) * 4)[:, None]
    quad = np.array([[0, 1, 2], [0, 2, 3]]) if outward else np.array([[0, 2, 1], [0, 3, 2]])
    triangles = (base[:, None, :] + quad[None, :, :]).reshape(-1, 3)
    soup.add(
        part,
        material,
        positions.reshape(-1, 3),
        triangles,
        uvs.reshape(-1, 2),
        normals.reshape(-1, 3),
    )


def prism(soup: Soup, part, material, shape, d0, d1, uv_origin=(0.0, 0.0), front=True, back=False):
    """A shape extruded out of the wall from d0 to d1: its front face and its sides."""
    for polygon in polygons_of(shape):
        if polygon.area < 1e-7:
            continue
        polygon = orient(polygon, 1.0)
        if front:
            face(soup, part, material, polygon, max(d0, d1), uv_origin)
        if back:
            face(soup, part, material, polygon, min(d0, d1), uv_origin, flip=True)
        ring_walls(
            soup, part, material, np.asarray(polygon.exterior.coords)[:-1], d0, d1, True, uv_origin
        )
        for hole in polygon.interiors:
            ring_walls(soup, part, material, np.asarray(hole.coords)[:-1], d0, d1, True, uv_origin)


def box(soup: Soup, part, material, u0, v0, u1, v1, d0, d1, uv_origin=(0.0, 0.0), back=False):
    prism(
        soup,
        part,
        material,
        Polygon([(u0, v0), (u1, v0), (u1, v1), (u0, v1)]),
        d0,
        d1,
        uv_origin,
        back=back,
    )


def extrude_profile(soup: Soup, part, material, u0, u1, profile, uv_origin=(0.0, 0.0)):
    """A moulding: a closed profile of (d, v) points, counter-clockwise seen from the left end, swept from u0 to u1.

    The profile's first and last points lie on the wall (d = 0) and that edge is left open.
    """
    profile = np.asarray(profile, float)
    if len(profile) < 2 or u1 - u0 < 1e-6:
        return
    a, b = profile[:-1], profile[1:]
    edge = b - a
    length = np.linalg.norm(edge, axis=1)
    keep = length > 1e-7
    a, b, edge, length = a[keep], b[keep], edge[keep], length[keep]
    count = len(a)
    run = np.concatenate([[0.0], np.cumsum(length)])
    # Sweeping along +u, a profile edge from a to b has the outward normal (0, -edge_d, edge_v) rotated: (dv, -dd).
    normal = np.column_stack([np.zeros(count), -edge[:, 0], edge[:, 1]]) / length[:, None]
    positions = np.empty((count, 4, 3))
    positions[:, 0] = np.column_stack([np.full(count, u0), a[:, 1], a[:, 0]])
    positions[:, 1] = np.column_stack([np.full(count, u1), a[:, 1], a[:, 0]])
    positions[:, 2] = np.column_stack([np.full(count, u1), b[:, 1], b[:, 0]])
    positions[:, 3] = np.column_stack([np.full(count, u0), b[:, 1], b[:, 0]])
    uvs = np.empty((count, 4, 2))
    uvs[:, 0] = np.column_stack([np.full(count, u0), run[:-1]])
    uvs[:, 1] = np.column_stack([np.full(count, u1), run[:-1]])
    uvs[:, 2] = np.column_stack([np.full(count, u1), run[1:]])
    uvs[:, 3] = np.column_stack([np.full(count, u0), run[1:]])
    uvs += np.asarray(uv_origin)
    normals = np.repeat(normal[:, None, :], 4, axis=1)
    base = (np.arange(count) * 4)[:, None, None]
    triangles = (base + np.array([[0, 1, 2], [0, 2, 3]])[None]).reshape(-1, 3)
    soup.add(
        part,
        material,
        positions.reshape(-1, 3),
        triangles,
        uvs.reshape(-1, 2),
        normals.reshape(-1, 3),
    )
    cap = Polygon(profile)
    if cap.is_valid and cap.area > 1e-7:
        vertices, tris = triangulate(cap)
        for u, flip in ((u0, True), (u1, False)):
            cap_positions = np.column_stack(
                [np.full(len(vertices), u), vertices[:, 1], vertices[:, 0]]
            )
            cap_normal = np.array([-1.0 if flip else 1.0, 0.0, 0.0])
            # (d, v) counter-clockwise is clockwise seen from +u, so the right cap reverses the winding.
            soup.add(
                part,
                material,
                cap_positions,
                tris if flip else tris[:, ::-1],
                np.column_stack([vertices[:, 0], vertices[:, 1]]) + np.asarray(uv_origin),
                np.tile(cap_normal, (len(vertices), 1)),
            )


def arch_polygon(width, height, head="flat", rise=None, segments=14) -> Polygon:
    """The outline of an opening with its bottom-left corner at the origin."""
    if head in (None, "flat"):
        return Polygon([(0, 0), (width, 0), (width, height), (0, height)])
    half = width / 2
    if head == "round":
        rise = half
    elif rise is None:
        rise = width * {"segmental": 0.14, "basket": 0.3, "shouldered": 0.12, "pointed": 0.75}.get(
            head, 0.14
        )
    rise = min(rise, height * 0.8)
    spring = height - rise
    if head == "basket":
        top = [
            (half + half * np.cos(t), spring + rise * np.sin(t))
            for t in np.linspace(0.0, np.pi, segments + 1)
        ]
    elif head == "pointed":
        # Two arcs struck from the opposite springing points, scaled to the rise.
        angles = np.linspace(0.0, np.pi / 3, segments // 2 + 1)
        right = [(width * np.cos(a), spring + rise * np.sin(a) / np.sin(np.pi / 3)) for a in angles]
        top = right + [(width - x, y) for x, y in reversed(right[:-1])]
    else:
        radius = (rise * rise + half * half) / (2 * rise)
        alpha = np.arcsin(min(1.0, half / radius))
        centre = spring + rise - radius
        top = [
            (half + radius * np.sin(a), centre + radius * np.cos(a))
            for a in np.linspace(alpha, -alpha, segments + 1)
        ]
    points = [(0.0, 0.0), (width, 0.0)] + [(float(x), float(y)) for x, y in top]
    return Polygon(points).buffer(0)


def cylinder(soup: Soup, part, material, u, d, v0, v1, radius, sides=12, top_radius=None):
    """An upright round shaft (smooth-shaded) with a flat top, centred at (u, d)."""
    top_radius = radius if top_radius is None else top_radius
    angles = np.arange(sides + 1) * 2 * np.pi / sides
    ring = np.column_stack([np.cos(angles), np.sin(angles)])
    low = np.column_stack(
        [u + radius * ring[:, 0], np.full(sides + 1, v0), d + radius * ring[:, 1]]
    )
    high = np.column_stack(
        [u + top_radius * ring[:, 0], np.full(sides + 1, v1), d + top_radius * ring[:, 1]]
    )
    normals = np.column_stack([ring[:, 0], np.zeros(sides + 1), ring[:, 1]])
    along = angles * radius
    index = np.arange(sides)
    # Seen from outside the shaft, going round with increasing angle runs towards -u at the front (d > 0).
    triangles = np.concatenate(
        [
            np.column_stack([index, index + sides + 1, index + 1]),
            np.column_stack([index + 1, index + sides + 1, index + sides + 2]),
        ]
    )
    soup.add(
        part,
        material,
        np.vstack([low, high]),
        triangles,
        np.vstack([np.column_stack([along, low[:, 1]]), np.column_stack([along, high[:, 1]])]),
        np.vstack([normals, normals]),
    )
    cap = np.vstack([[u, v1, d], high[:-1]])
    fan = np.column_stack([np.zeros(sides, np.int64), 1 + (index + 1) % sides, 1 + index])
    soup.add(part, material, cap, fan, cap[:, [0, 2]], np.tile([0.0, 1.0, 0.0], (len(cap), 1)))
