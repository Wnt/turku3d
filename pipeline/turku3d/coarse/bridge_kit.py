"""Parametric bridge components fitted in the crossing's measured local frame.

Dimensions are estimates unless supplied by a source tag. Geometry is shared by
the visual and collision producers; no independent invisible obstruction meshes.
"""

import numpy as np
import shapely

from turku3d.structures.geometry import Faces


def tapered_post(key, centre, direction, levels, surface="granite"):
    """Closed square shaft with (height, along width, across width) sections."""
    from .bridge_details import appearance

    centre = np.asarray(centre, float)
    direction = np.asarray(direction, float)
    direction /= np.linalg.norm(direction)
    normal = np.array([-direction[1], direction[0]])
    rings = []
    for height, length, width in levels:
        xz = centre[[0, 2]] + np.array([
            -direction * length / 2 - normal * width / 2,
            direction * length / 2 - normal * width / 2,
            direction * length / 2 + normal * width / 2,
            -direction * length / 2 + normal * width / 2,
        ])
        rings.append(np.c_[xz[:, 0], np.full(4, centre[1] + height), xz[:, 1]])
    faces = Faces()
    # XZ counterclockwise is downward in engine XYZ.
    faces.face(rings[0], rings[0][:, [0, 2]] - centre[[0, 2]])
    faces.face(rings[-1][::-1], rings[-1][::-1][:, [0, 2]] - centre[[0, 2]])
    for bottom, top in zip(rings[:-1], rings[1:], strict=True):
        for i in range(4):
            j = (i + 1) % 4
            faces.face([bottom[j], bottom[i], top[i], top[j]])
    return appearance(faces.mesh(key, "wall"), surface)


def entrance_points(way, inset, setback=0):
    """Inset each longitudinal edge from its own abutment corner.

    A cross-section near a skew end can intersect the end face inside a lane.
    Only long side chains are eligible; ambiguous fragmented outlines abstain.
    """
    from .bridge_details import deck_outline

    axis, outline = way["line"], deck_outline(way)
    sides = []
    for part in shapely.get_parts(outline):
        if part.geom_type != "Polygon":
            continue
        coords = np.asarray(part.exterior.coords)
        for a, b in zip(coords[:-1], coords[1:], strict=True):
            middle = shapely.Point((a + b) / 2)
            station = axis.project(middle)
            tangent = np.asarray(axis.interpolate(min(axis.length, station + .5)).coords[0])
            tangent -= np.asarray(axis.interpolate(max(0, station - .5)).coords[0])
            edge = b - a
            if abs(edge @ tangent) >= .8 * np.linalg.norm(edge) * np.linalg.norm(tangent):
                sides.append(shapely.LineString([a, b]))
    chains = [p for p in shapely.get_parts(shapely.line_merge(shapely.union_all(sides)))
              if p.geom_type == "LineString" and p.length > axis.length * .6]
    if len(chains) != 2:
        return []
    points = []
    for chain in chains:
        for start in (True, False):
            # Slide along the edge until the entire plinth clears a skew end.
            for extra in np.arange(0, max(.01, 4 * setback) + .01, .1):
                distance = inset + extra
                station = distance if start else chain.length - distance
                if not 0 < station < chain.length or distance > chain.length / 4:
                    continue
                p = np.asarray(chain.interpolate(station).coords[0])
                d = np.asarray(chain.interpolate(min(chain.length, station + .2)).coords[0])
                d -= np.asarray(chain.interpolate(max(0, station - .2)).coords[0])
                d /= np.linalg.norm(d)
                n = np.array([-d[1], d[0]])
                towards = np.asarray(axis.interpolate(axis.project(shapely.Point(p))).coords[0]) - p
                if n @ towards < 0:
                    n *= -1
                p += n * setback
                if setback:
                    footprint = shapely.Polygon([p + d * u * setback + n * v * setback
                                                 for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
                    if not outline.buffer(.001).covers(footprint):
                        continue
                points.append((p, d))
                break
    return points


def entrances(way, height_at, blocked, spec):
    """Four tapered masonry end pylons with layered plinths and bronze collars."""
    height = spec.get("entrance_height", 0)
    if height <= 0 or way.get("landing") or way["line"].length < 12:
        return []
    key = f"draft-bridge:{way['id']}:entrance"
    width = spec.get("entrance_width", 1.05)
    result = []
    half = width * 1.22 / 2
    from .bridge_details import deck_outline

    for p, direction in entrance_points(way, spec.get("entrance_inset", 1.2), half + .02):
        # A same-level source obstacle takes priority over estimated ornament.
        normal = np.array([-direction[1], direction[0]])
        footprint = shapely.Polygon([p + direction * u * half + normal * v * half
                                     for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
        if not deck_outline(way).buffer(.001).covers(footprint) or any(
            footprint.intersects(g) for g in blocked
        ):
            continue
        y = float(height_at(np.asarray([p]))[0])
        levels = [(0, 1.22, 1.22), (.19, 1.22, 1.22),
                  (.22, 1.06, 1.06), (.95, 1.06, 1.06),
                  (1.02, .92, .92), (1.12, .92, .92),
                  (1.14, .77, .77), (height - .22, .49, .49),
                  (height - .09, .49, .49), (height, .31, .31)]
        levels = [(h, a * width, b * width) for h, a, b in levels]
        result.append(tapered_post(key, [p[0], y, p[1]], direction, levels))
        normal = np.array([-direction[1], direction[0]])
        # The same collar module works at any yaw; modest relief avoids decals.
        collar = height - .85
        for lift, size in ((collar, .64), (collar + .08, .72)):
            result.append(tapered_post(key, [p[0], y, p[1]], direction,
                                      [(lift, size * width, size * width),
                                       (lift + .045, size * width, size * width)], "bronze"))
        for sign in (-1, 1):
            for offset in (-.16, 0, .16):
                from .bridge_details import tube

                endpoints = []
                for lift in (collar - 1.05, collar + .14):
                    shaft_width = np.interp(lift, [1.14, height - .22], [.77, .49]) * width
                    c = p + normal * sign * (shaft_width / 2 + .008) + direction * offset * width
                    endpoints.append([c[0], y + lift, c[1]])
                result.append(tube(key, *endpoints, .018, "bronze", "wall", sides=4))
    return result


def cross_section(way, station, inset=0, polygon=None):
    """Local tangent and the containing deck section, including skew outlines."""
    from .bridge_details import deck_outline

    axis = way["line"]
    centre = np.asarray(axis.interpolate(station).coords[0])
    direction = np.asarray(axis.interpolate(min(axis.length, station + .2)).coords[0])
    direction -= np.asarray(axis.interpolate(max(0, station - .2)).coords[0])
    direction /= np.linalg.norm(direction)
    normal = np.array([-direction[1], direction[0]])
    outline = deck_outline(way) if polygon is None else polygon
    bounds = np.asarray(outline.bounds).reshape(2, 2)
    reach = np.linalg.norm(bounds[1] - bounds[0]) + np.linalg.norm(centre - bounds[0]) + 1
    ray = shapely.LineString([centre - normal * reach, centre + normal * reach])
    sections = [p for p in shapely.get_parts(ray.intersection(outline))
                if p.geom_type == "LineString" and p.distance(shapely.Point(centre)) < .001]
    if len(sections) != 1 or sections[0].length <= 2 * inset + 1:
        return None
    offsets = (np.asarray(sections[0].coords) - centre) @ normal
    edges = [centre + normal * (offsets.min() + inset),
             centre + normal * (offsets.max() - inset)]
    return centre, direction, normal, edges


def rounded_pier(key, a, b, bottom, top, radius=.7, surface="stone"):
    """Closed cutwater pier: a transverse stadium plan with round river ends."""
    from .bridge_details import appearance

    a, b = np.asarray(a, float), np.asarray(b, float)
    direction = (b - a) / np.linalg.norm(b - a)
    normal = np.array([-direction[1], direction[0]])
    angle = np.linspace(-np.pi / 2, np.pi / 2, 9)
    arc = radius * (np.cos(angle)[:, None] * direction + np.sin(angle)[:, None] * normal)
    ring = np.concatenate([b + arc, a - arc])
    lower = np.c_[ring[:, 0], np.full(len(ring), bottom), ring[:, 1]]
    upper = lower.copy()
    upper[:, 1] = top
    faces = Faces()
    faces.face(lower, ring - a)
    faces.face(upper[::-1], (ring - a)[::-1])
    perimeter = np.r_[0, np.cumsum(np.linalg.norm(np.diff(np.r_[ring, ring[:1]], axis=0), axis=1))]
    for i in range(len(ring)):
        j = (i + 1) % len(ring)
        faces.face([lower[j], lower[i], upper[i], upper[j]],
                   [[perimeter[i + 1], bottom], [perimeter[i], bottom],
                    [perimeter[i], top], [perimeter[i + 1], top]])
    return appearance(faces.mesh(key, "wall"), surface)


def stay_clear(a, b, radius, height_at, polygon, obstacles):
    """Check the swept rod, including its interior, against deck walking space."""
    a, b = np.asarray(a), np.asarray(b)
    line = shapely.LineString([a[[0, 2]], b[[0, 2]]])
    swept = line.buffer(radius)
    if not polygon.covers(swept) or any(swept.intersects(g) for g in obstacles):
        return False
    # Slice the whole rod at 10 cm intervals, including radius and a margin for
    # the intervening segment. Endpoints alone miss a head-height intrusion.
    t = np.linspace(0, 1, max(2, int(np.ceil(np.linalg.norm(b - a) / .1)) + 1))
    points = a + t[:, None] * (b - a)
    lift = points[:, 1] - height_at(points[:, [0, 2]])
    head = points[(lift > .1 - radius) & (lift < 2.4 + radius)]
    corridor = polygon.buffer(-.6 + radius + .1)
    return not len(head) or not shapely.intersects_xy(corridor, head[:, 0], head[:, 2]).any()


def stayed_span(way, height_at, sample_ground, blocked, polygon, spec, *, obstacles=()):
    """Paired towers, rods and low rounded footings in the measured deck frame.

    Surveyed pier stations take priority. Unsupported cable-stayed mappings use
    conservative fifth-span estimates; all dimensions are source/profile driven.
    Every tube and footing is shared with collision, without hidden wall planes.
    """
    from .bridge_details import tube
    from .water import LEVEL

    axis = way["line"]
    if spec["structure"] != "cable-stayed" or way.get("landing") or axis.length < 20:
        return []
    key = f"draft-bridge:{way['id']}:stays"
    survey = way.get("survey", {})
    measured = survey.get("pier_stations", [])
    stations = [axis.length * f for f in spec.get("tower_fractions", (.2, .8))]
    # Sparse returns fit a declared topology, never invent or remove towers.
    available = set(float(s) for s in measured if axis.length * .1 < s < axis.length * .9)
    for i, station in enumerate(stations):
        if available:
            nearest = min(available, key=lambda s: abs(s - station))
            if abs(nearest - station) < axis.length * .15:
                stations[i] = nearest
                available.remove(nearest)
    stations.sort()
    limits = [1., *[(a + b) / 2 for a, b in zip(stations[:-1], stations[1:], strict=True)],
              axis.length - 1.]
    height = spec.get("tower_height", min(12., max(4., axis.length * .085)))
    radius = spec.get("tower_radius", .17)
    meshes = []
    for i, station in enumerate(stations):
        section = cross_section(way, station, radius + .08, polygon)
        if section is None:
            return []
        centre, direction, normal, edges = section
        footprint = shapely.union_all([shapely.Point(p).buffer(.8) for p in edges])
        if any(footprint.intersects(g) for g in blocked) or not polygon.buffer(.8).covers(footprint):
            return []
        deck_y = height_at(np.asarray(edges))
        base = float(np.min(sample_ground(np.asarray(edges)))) - .4
        for s, y in zip(measured, survey.get("pier_bases", []), strict=True):
            if abs(s - station) < 1:
                base = min(base, float(y) - .3)
        # Water-level masonry cutwaters carry slender metal shafts to the deck.
        footing_top = min(survey.get("water_level", LEVEL) + spec.get("pier_freeboard", 1.0),
                          float(deck_y.min()) - spec["thickness"] - .6)
        if footing_top <= base:
            return []
        for side, (p, y) in enumerate(zip(edges, deck_y, strict=True)):
            meshes.append(rounded_pier(key, p - direction * .12, p + direction * .12,
                                       base, footing_top, surface=spec["support"]))
            top = [p[0], y + height, p[1]]
            meshes.append(tube(key, [p[0], footing_top, p[1]], top, radius, "steel", "wall", 10))
            for target in limits[i:i + 2]:
                # Widened viewing platforms can make a middle-span anchor unsafe.
                # Search back along the real edge for the longest clear straight
                # stay, retaining a useful span rather than a stub beside a tower.
                candidates = np.linspace(target, station, int(abs(target - station) / .5) + 2)
                pair = None
                for attachment in candidates:
                    if abs(attachment - station) < max(4., abs(target - station) * .35):
                        break
                    anchor_section = cross_section(way, attachment, radius + .08, polygon)
                    if anchor_section is None:
                        continue
                    anchor = anchor_section[3][side]
                    anchor_y = float(height_at(np.asarray([anchor]))[0]) + .18
                    ends = [([p[0], y + height - lift, p[1]],
                             [anchor[0], anchor_y, anchor[1]]) for lift in (.35, .8)]
                    if all(stay_clear(a, b, .035, height_at, polygon, obstacles) for a, b in ends):
                        pair = ends
                        break
                if pair is None:
                    return []
                meshes.extend(tube(key, a, b, .035, "steel", "railing") for a, b in pair)
                meshes.append(tapered_post(key, [anchor[0], anchor_y - .18, anchor[1]],
                                           anchor_section[1], [(0, .32, .22), (.05, .32, .22),
                                                               (.22, .12, .12)], "steel"))
        # A slim header closes the portal above pedestrian head clearance.
        meshes.append(tube(key, [edges[0][0], deck_y[0] + height - .65, edges[0][1]],
                           [edges[1][0], deck_y[1] + height - .65, edges[1][1]],
                           .085, "steel", "wall"))
    return meshes


def deck_framing(way, height_at, blocked, polygon, spec):
    """Local transverse steel framing, omitted over protected lower passages."""
    from .bridge_details import wall

    if (spec["body"] != "steel" or spec["structure"] not in ("beam", "cable-stayed")
            or way.get("landing") or spec.get("box_depth")):
        return []
    key = f"draft-bridge:{way['id']}:framing"
    meshes = []
    for station in np.arange(1., way['line'].length - .5, spec['floor_beam_spacing']):
        section = cross_section(way, station, .2, polygon)
        if section is None:
            continue
        _, direction, _, edges = section
        line = np.asarray(edges)
        footprint = shapely.LineString(line).buffer(.07, cap_style='square')
        # Lower passages were solved against the slab soffit. Do not add a beam
        # below that clearance envelope, even when the deck itself is valid.
        if not polygon.covers(footprint) or any(footprint.intersects(g) for g in blocked):
            continue
        top = height_at(line) - spec['thickness']
        meshes.append(wall(key, line, top - spec['floor_beam_depth'], top, .14, 'steel'))
        for edge, y in zip(edges, top, strict=True):
            meshes.append(tapered_post(key, [edge[0], y, edge[1]], direction,
                                       [(0, .12, .12), (spec['thickness'], .12, .12)], 'steel'))
    return meshes


def support_stations(way, spec):
    """Fit qualified survey returns to a declared or span-length support topology."""
    length = way['line'].length
    spans = max(1, int(np.ceil(length / (35 if spec['foot'] else 40))))
    stations = [length * f for f in spec.get('pier_fractions',
                 np.linspace(0, 1, spans + 1)[1:-1])]
    available = set(float(s) for s in way.get('survey', {}).get('pier_stations', [])
                    if .15 * length < s < .85 * length)
    for i, station in enumerate(stations):
        if available:
            nearest = min(available, key=lambda s: abs(s - station))
            if abs(nearest - station) < .12 * length:
                stations[i] = nearest
                available.remove(nearest)
    return sorted(stations)


def beam_supports(way, height_at, sample_ground, blocked, polygon, spec):
    """Rounded full-width cutwaters with caps below the actual local deck soffit."""
    if way.get('landing') or spec['structure'] in ('arch', 'humpback', 'suspension', 'cable-stayed'):
        return []
    key = f"draft-bridge:{way['id']}:piers"
    meshes = []
    radius = spec.get('pier_radius', .6 if spec['foot'] else .9)
    for station in support_stations(way, spec):
        section = cross_section(way, station, radius + .35, polygon)
        if section is None:
            continue
        centre, _, _, edges = section
        # Preserve open river views under deck cantilevers; the shaft fills 70%
        # of the usable local width, with rounded ends and a slightly wider cap.
        ends = centre + (np.asarray(edges) - centre) * .7
        footprint = shapely.LineString(ends).buffer(radius + .15)
        if not polygon.covers(footprint) or any(footprint.intersects(g) for g in blocked):
            continue
        cap_points = np.asarray(footprint.exterior.coords)
        top = float(np.min(height_at(cap_points))) - spec['thickness'] - spec.get('box_depth', 0)
        bottom = float(np.min(sample_ground(ends))) - .4
        survey = way.get('survey', {})
        for measured, base in zip(survey.get('pier_stations', []), survey.get('pier_bases', []), strict=True):
            if abs(station - measured) < 1:
                bottom = min(bottom, float(base) - .3)
        if top - bottom < 1:
            continue
        meshes.append(rounded_pier(key, *ends, bottom, top - .25, radius, spec['support']))
        meshes.append(rounded_pier(key, *ends, top - .25, top, radius + .15, spec['support']))
    return meshes


def box_girder(way, height_at, blocked, polygon, spec):
    """Closed shallow trapezoidal box sections follow the lidar crown and local width.

    Lower passages retain their solved clearance envelope: an intersecting section
    is omitted. The deck itself remains the sole walking/driving surface.
    """
    depth = spec.get('box_depth', 0)
    if depth <= 0 or way.get('landing'):
        return []
    axis = way['line']
    sections = []
    for station in np.linspace(.15, axis.length - .15, max(2, int(np.ceil(axis.length / 2)) + 1)):
        section = cross_section(way, station, .3, polygon)
        if section is None:
            sections.append(None)
            continue
        centre, _, _, edges = section
        upper = np.asarray(edges)
        lower = centre + (upper - centre) * .78
        top = height_at(upper) - spec['thickness']
        # Clockwise in cross-section when viewed along the positive axis.
        sections.append(np.array([[upper[0, 0], top[0], upper[0, 1]],
                                  [upper[1, 0], top[1], upper[1, 1]],
                                  [lower[1, 0], top[1] - depth, lower[1, 1]],
                                  [lower[0, 0], top[0] - depth, lower[0, 1]]]))
    from .bridge_details import appearance
    faces = Faces()
    for a, b in zip(sections[:-1], sections[1:], strict=True):
        if a is None or b is None:
            continue
        footprint = shapely.MultiPoint(np.r_[a[:, [0, 2]], b[:, [0, 2]]]).convex_hull
        if not polygon.covers(footprint) or any(footprint.intersects(g) for g in blocked):
            continue
        # Each station interval is closed, so collision cannot develop open sides
        # when a protected lower passage interrupts the cladding.
        faces.face(a[::-1])
        faces.face(b)
        for i in range(4):
            j = (i + 1) % 4
            faces.face([a[i], a[j], b[j], b[i]])
    return [appearance(faces.mesh(f"draft-bridge:{way['id']}:box", 'wall'), 'steel')] if faces.indices else []
