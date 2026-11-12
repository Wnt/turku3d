"""The roof over the shell's roof planes: covering with slope-aligned UVs, eaves and verges that overhang the walls,
fascia and soffit, brackets, and chimneys standing on the planes. Survey axes (E, N, H), local to the building.
"""

import numpy as np
from shapely.geometry import Point, Polygon

from . import mesh
from .materials import Palette

UP = np.array([0.0, 0.0, 1.0])


class _Frame:
    """A wall-like frame for parts built with the local (u, v, d) builders."""

    def __init__(self, origin, u_dir, normal):
        self.origin, self.u_dir, self.normal = (
            np.asarray(origin, float),
            np.asarray(u_dir, float),
            np.asarray(normal, float),
        )


def _plane_z(plane, xy):
    n, p0 = plane.normal, plane.ring[0]
    xy = np.asarray(xy, float)
    return p0[2] - (n[0] * (xy[..., 0] - p0[0]) + n[1] * (xy[..., 1] - p0[1])) / n[2]


def _segment_distance(point, a, b):
    ab = b - a
    t = np.clip(((point - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return float(np.linalg.norm(point - (a + t * ab)))


def _edge_kinds(planes, facades, footprint):
    """Per plane, per edge: True when the edge is free (an eave or a verge over a wall top)."""
    from shapely.geometry import Point

    segments = [
        (k, plane.ring[i], plane.ring[(i + 1) % len(plane.ring)])
        for k, plane in enumerate(planes)
        for i in range(len(plane.ring))
    ]
    out = []
    boundary = (
        footprint.exterior
        if footprint is not None and not footprint.is_empty and footprint.geom_type == "Polygon"
        else None
    )
    for k, plane in enumerate(planes):
        ring = plane.ring
        free = []
        for i in range(len(ring)):
            a, b = ring[i], ring[(i + 1) % len(ring)]
            middle = (a + b) / 2
            direction = b - a
            length = np.linalg.norm(direction)
            if length < 1e-6:
                free.append(False)
                continue
            # Shared with another roof plane (a ridge, hip or valley), wholly or along part of a longer edge.
            shared = False
            for other, c, d in segments:
                if other == k:
                    continue
                other_direction = d - c
                other_length = np.linalg.norm(other_direction)
                if (
                    other_length < 1e-6
                    or abs(float(direction @ other_direction)) / (length * other_length) < 0.98
                ):
                    continue
                if _segment_distance(middle, c, d) < 0.1:
                    shared = True
                    break
            if shared:
                free.append(False)
                continue
            verdict = None
            for facade in facades:
                relative = middle - facade.origin
                if abs(float(relative @ facade.normal)) > 0.2:
                    continue
                u = float(relative @ facade.u_dir)
                if u < -0.1 or u > facade.width + 0.1:
                    continue
                if (
                    abs(float(direction[:2] @ facade.u_dir[:2]))
                    / max(np.linalg.norm(direction[:2]), 1e-9)
                    < 0.9
                ):
                    continue
                top = _outline_top(facade, u)
                if top is None:
                    continue
                verdict = top <= relative[2] + 0.4
                break
            if verdict is None:
                verdict = boundary is not None and boundary.distance(Point(middle[:2])) < 0.25
            free.append(bool(verdict))
        out.append(free)
    return out


def _outline_top(facade, u):
    from shapely.geometry import LineString

    cut = facade.outline.intersection(LineString([(u, -5.0), (u, 1e4)]))
    return None if cut.is_empty else cut.bounds[3]


def _line_meet(p, d, q, e):
    """Intersection of lines p + s d and q + t e in the plan, or None when they are parallel."""
    cross = d[0] * e[1] - d[1] * e[0]
    if abs(cross) < 1e-4:
        return None
    s = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / cross
    return p + s * d


def build(
    soup: mesh.Soup,
    shell,
    roof: dict,
    palette: Palette,
    trim_colour: str,
    wall_family: str,
    facade_by_wall: dict,
    report: list,
):
    planes = shell.roofs
    if not planes:
        return
    cover = palette.key(roof.get("material"))
    eaves, verge = roof.get("eaves", {}), roof.get("verge", {})
    eave_out = eaves.get("overhang_m", 0.3)
    verge_out = verge.get("overhang_m", 0.15)
    fascia = eaves.get("fascia_m", 0.18)
    edge_colour = eaves.get("colour", trim_colour)
    edge_family = (
        "painted_wood" if wall_family in ("weatherboard", "log", "painted_wood") else "plaster"
    )
    edge_key = palette.key(
        {
            "family": edge_family,
            "colour": edge_colour,
            **({"finish": "smooth"} if edge_family == "plaster" else {}),
        }
    )
    verge_key = palette.key(
        {
            "family": edge_family,
            "colour": verge.get("colour", edge_colour),
            **({"finish": "smooth"} if edge_family == "plaster" else {}),
        }
    )
    free = _edge_kinds(planes, shell.facades, shell.footprint)
    brackets = eaves.get("brackets")

    for plane, plane_free in zip(planes, free, strict=True):
        ring = plane.ring
        count = len(ring)
        plan = ring[:, :2]
        offsets, normals2 = [], []
        for i in range(count):
            a, b = ring[i], ring[(i + 1) % count]
            direction = (b - a)[:2]
            length = np.linalg.norm(direction)
            normal = (
                np.array([direction[1], -direction[0]]) / length if length > 1e-9 else np.zeros(2)
            )
            sloped = abs(b[2] - a[2]) > 0.08 * max(length, 1e-6)
            low = min(a[2], b[2]) <= ring[:, 2].min() + 0.35 * max(
                0.5, ring[:, 2].max() - ring[:, 2].min()
            )
            amount = (verge_out if sloped or not low else eave_out) if plane_free[i] else 0.0
            if plane.normal[2] > 0.999:
                amount = min(amount, 0.2) if plane_free[i] else 0.0
            offsets.append(amount)
            normals2.append(normal)
        moved = np.empty_like(plan)
        for i in range(count):
            prev = (i - 1) % count
            o_prev, o_next = offsets[prev], offsets[i]
            if o_prev == 0.0 and o_next == 0.0:
                moved[i] = plan[i]
                continue
            p = plan[prev] + normals2[prev] * o_prev
            q = plan[i] + normals2[i] * o_next
            meet = _line_meet(p, plan[i] - plan[prev], q, plan[(i + 1) % count] - plan[i])
            simple = plan[i] + normals2[prev] * o_prev + normals2[i] * o_next
            if meet is None or np.linalg.norm(meet - plan[i]) > 3.0 * max(o_prev, o_next) + 0.05:
                meet = simple
            moved[i] = meet
        top = np.column_stack([moved, _plane_z(plane, moved)])

        # Covering, with u along the eaves and v up the slope so that seams and tile rows follow the roof.
        slope = UP - plane.normal * plane.normal[2]
        slope = (
            slope / np.linalg.norm(slope)
            if np.linalg.norm(slope) > 1e-4
            else np.array([0.0, 1.0, 0.0])
        )
        level = np.cross(slope, plane.normal)
        polygon = Polygon(moved, [h[:, :2] for h in plane.holes])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        for piece in mesh.polygons_of(polygon):
            vertices, triangles = mesh.triangulate(piece)
            if len(triangles) == 0:
                continue
            positions = np.column_stack([vertices, _plane_z(plane, vertices)])
            uvs = np.column_stack([positions @ level, positions @ slope])
            soup.add(
                "roof", cover, positions, triangles, uvs, np.tile(plane.normal, (len(positions), 1))
            )

        # Eaves and verges: the underside of the overhang and the edge board.
        for i in range(count):
            if offsets[i] <= 0.0:
                continue
            j = (i + 1) % count
            inner_a, inner_b = ring[i], ring[j]
            outer_a, outer_b = top[i], top[j]
            drop = np.array([0.0, 0.0, fascia])
            is_verge = abs(inner_b[2] - inner_a[2]) > 0.08 * np.linalg.norm((inner_b - inner_a)[:2])
            key = verge_key if is_verge else edge_key
            # Soffit, facing down: inner edge on the wall line, outer edge under the roof edge.
            soffit = np.array([inner_a - drop, outer_a - drop, outer_b - drop, inner_b - drop])
            normal = np.cross(soffit[1] - soffit[0], soffit[2] - soffit[0])
            order = [[0, 1, 2], [0, 2, 3]] if normal[2] < 0 else [[0, 2, 1], [0, 3, 2]]
            along = np.linalg.norm(inner_b - inner_a)
            soup.add(
                "eaves",
                key,
                soffit,
                order,
                [[0, 0], [0, offsets[i]], [along, offsets[i]], [along, 0]],
            )
            board = np.array([outer_a - drop, outer_b - drop, outer_b, outer_a])
            outward = np.append(normals2[i], 0.0)
            normal = np.cross(board[1] - board[0], board[2] - board[0])
            order = [[0, 1, 2], [0, 2, 3]] if normal @ outward > 0 else [[0, 2, 1], [0, 3, 2]]
            soup.add("eaves", key, board, order, [[0, 0], [along, 0], [along, fascia], [0, fascia]])
            if brackets and offsets[i] >= 0.25:
                _brackets(
                    soup,
                    palette,
                    edge_key if not is_verge else verge_key,
                    inner_a,
                    inner_b,
                    normals2[i],
                    offsets[i],
                    fascia,
                    brackets,
                )

    for chimney in roof.get("chimneys", []):
        _chimney(soup, shell, chimney, palette, facade_by_wall, report)


def _brackets(soup, palette, key, a, b, outward2, overhang, fascia, spec):
    """Carved brackets under an overhang, spaced along the wall line from a to b."""
    spacing = spec.get("spacing_m", 1.0)
    size = spec.get("size_m", 0.4)
    length = float(np.linalg.norm(b - a))
    if length < spacing * 0.6:
        return
    count = max(1, int(length / spacing))
    reach = min(overhang * 0.9, size * 1.1)
    if spec.get("kind") == "plain":
        profile = [(0.0, 0.0), (reach, 0.0), (0.0, -size)]
    else:
        profile = [
            (0.0, 0.0),
            (reach, 0.0),
            (reach, -size * 0.2),
            (reach * 0.45, -size * 0.38),
            (reach * 0.22, -size * 0.8),
            (0.0, -size),
        ]
    thick = 0.07
    local = mesh.Soup()
    polygon = Polygon([(d, v) for d, v in profile]).buffer(0)
    # A bracket is a (d, v) profile with thickness along the wall; build it as a prism lying in a turned frame.
    mesh.prism(local, "eaves", key, polygon, -thick / 2, thick / 2, back=True)
    out3 = np.append(outward2, 0.0)
    for k in range(count):
        t = (k + 0.5) / count
        origin = a + (b - a) * t - np.array([0.0, 0.0, fascia])
        # A frame whose u runs out of the wall and whose d runs along it: the prism's faces look along the wall.
        soup.place(local, _Frame(origin, out3, np.cross(out3, UP)))


def _roof_height(shell, xy):
    best = None
    for plane in shell.roofs:
        polygon = Polygon(plane.ring[:, :2])
        if polygon.is_valid and polygon.buffer(0.05).contains(Point(xy)):
            z = float(_plane_z(plane, np.asarray(xy)))
            best = z if best is None else max(best, z)
    return best


def _chimney(soup, shell, chimney, palette: Palette, facade_by_wall, report):
    anchor = chimney.get("anchor", {})
    if "at" in anchor:
        centre = np.asarray(anchor["at"], float)
        u_dir = shell.facades[0].u_dir if shell.facades else np.array([1.0, 0.0, 0.0])
    elif anchor.get("wall") in facade_by_wall:
        facade = facade_by_wall[anchor["wall"]]
        centre = facade.point(
            anchor.get("u_m", facade.width / 2), 0.0, -anchor.get("setback_m", 3.0)
        )[:2]
        u_dir = facade.u_dir
    else:
        report.append("chimney without a usable anchor")
        return
    w, d, h = chimney.get("w_m", 0.9), chimney.get("d_m", 0.6), chimney.get("h_m", 1.2)
    side = np.array([-u_dir[1], u_dir[0], 0.0])
    corners = [
        centre + u_dir[:2] * sx * w / 2 + side[:2] * sy * d / 2 for sx in (-1, 1) for sy in (-1, 1)
    ]
    heights = [z for z in (_roof_height(shell, c) for c in corners) if z is not None]
    if not heights:
        report.append("chimney anchor is not over the roof")
        return
    low, high = min(heights) - 0.3, max(heights) + h
    key = palette.key(
        chimney.get("material"),
        fallback={"family": "brick", "colour": "#8a4b3a", "colour2": "#b9b2a6"},
    )
    frame = _Frame(
        np.array([centre[0], centre[1], 0.0]) - u_dir * w / 2 - side * d / 2, u_dir, -side
    )
    local = mesh.Soup()
    mesh.box(local, "chimneys", key, 0.0, low, w, high, -d, 0.0, back=True)
    top = np.array([[0.0, high, -d], [w, high, -d], [w, high, 0.0], [0.0, high, 0.0]])
    cap = chimney.get("cap", "slab")
    if cap in ("slab", "corbelled"):
        cap_key = palette.key({"family": "concrete", "colour": "#8b8780"}) if cap == "slab" else key
        mesh.box(
            local,
            "chimneys",
            cap_key,
            -0.06,
            high,
            w + 0.06,
            high + 0.08,
            -d - 0.06,
            0.06,
            back=True,
        )
        lid = np.array(
            [
                [-0.06, high + 0.08, -d - 0.06],
                [w + 0.06, high + 0.08, -d - 0.06],
                [w + 0.06, high + 0.08, 0.06],
                [-0.06, high + 0.08, 0.06],
            ]
        )
        local.add("chimneys", cap_key, lid, [[0, 2, 1], [0, 3, 2]], lid[:, [0, 2]])
    elif cap == "metal_hood":
        metal = palette.key({"family": "metal", "colour": "#2f3133"})
        mesh.box(
            local,
            "chimneys",
            metal,
            -0.04,
            high + 0.15,
            w + 0.04,
            high + 0.2,
            -d - 0.04,
            0.04,
            back=True,
        )
        local.add("chimneys", key, top, [[0, 2, 1], [0, 3, 2]], top[:, [0, 2]])
    else:
        local.add("chimneys", palette.flat("#1c1c1c"), top, [[0, 2, 1], [0, 3, 2]], top[:, [0, 2]])
        if cap == "pots":
            pot = palette.key({"family": "clay_tile", "colour": "#9a5a3c"})
            for x in np.linspace(w * 0.25, w * 0.75, max(1, round(w / 0.4))):
                mesh.cylinder(local, "chimneys", pot, x, -d / 2, high, high + 0.3, 0.09, sides=8)
    soup.place(local, frame)
