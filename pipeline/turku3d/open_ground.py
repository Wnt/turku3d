"""Open ground floors under raised OSM building parts (building:min_level / min_height).

A raised part with no ground-level part beneath it leaves its footprint open up
to the part's lower edge. Shell walls along that edge are cut like a ground-level
through-colonnade (visual and collision), round columns stand at a bounded prior
spacing, a soffit closes the volume above and the enclosed remainder of the
footprint gets an inner closing wall. The terrain continues beneath. Reviewed
cut components on the same wall always win over this prior.
"""

import numpy as np
import shapely

COLUMN_SPACING = 6.0  # metres between pilotis along an open edge (bounded prior)
COLUMN_WIDTH = 0.32
MIN_OPEN_HEIGHT = 2.1
MIN_VOLUME_ABOVE = 2.5  # a raised part leaves at least one storey above its soffit
MIN_AREA = 10.0
CUTS = {"bay", "recess", "loggia", "arcade", "through_cut"}
SOFFIT = (0.62, 0.61, 0.58, 1)


def pieces(building):
    """Open polygons in the footprint frame (engine x, z) with their absolute soffit height."""
    if isinstance(building, _with_pieces):
        return building._pieces
    result = []
    for piece, low, *ground in getattr(building, "open_ground", None) or ():
        level = ground[0] if ground else building.base
        top = level + low
        if (top - level >= MIN_OPEN_HEIGHT and building.top - top >= MIN_VOLUME_ABOVE
                and piece.area >= MIN_AREA):
            result.append((piece, top))
    return result


def edge_plans(vertices, building, admitted=()):
    """Through-cut plans for the parts of one shell wall that bound an open piece."""
    from turku3d.facade_component_geometry import frame

    open_pieces = pieces(building)
    if not open_pieces:
        return []
    tangent, normal = frame(vertices)
    length = float((vertices[1] - vertices[0]) @ tangent)
    bottom, wall_top = float(vertices[:2, 1].max()), float(vertices[2:, 1].min())
    if length < 1.5:
        return []
    samples = np.arange(0.25, length, 0.5)
    inside = vertices[0] + samples[:, None] * tangent - 0.3 * normal
    result = []
    for piece, top in open_pieces:
        # The LoD2 shell must enclose the soffit; a lower shell contradicts the part.
        if top > wall_top - 0.3 or top - bottom < MIN_OPEN_HEIGHT:
            continue
        covered = shapely.contains_xy(piece, inside[:, 0], inside[:, 2])
        edges = np.flatnonzero(np.diff(np.r_[0, covered.astype(int), 0]))
        for first, last in zip(edges[::2], edges[1::2], strict=True):
            left = max(0.0, samples[first] - 0.25)
            right = min(length, samples[last - 1] + 0.25)
            if right - left < 1.5:
                continue
            rect = shapely.box(left, bottom, right, top)
            if any(p["kind"] in CUTS and p["rect"].intersection(rect).area > 1e-6 for p in admitted):
                continue
            count = max(2, int(round((right - left) / COLUMN_SPACING)) + 1)
            margin = COLUMN_WIDTH * 0.65 + 0.05
            result.append(
                dict(
                    kind="arcade",
                    source="osm raised part",
                    through=True,
                    rect=rect,
                    metric=[left, bottom, right, top],
                    tangent=tangent,
                    normal=normal,
                    depth_m=0.3,
                    count=count,
                    rows=1,
                    head="flat",
                    column_centres=np.linspace(left + margin, right - margin, count),
                    column_style=dict(shape="round", width=COLUMN_WIDTH, colour=None),
                    returns=[left > 0.01, right < length - 0.01, False, True],
                )
            )
    return result


def _oriented(points, outward):
    n = np.cross(points[3] - points[0], points[2] - points[0])
    return points if n @ outward > 0 else points[::-1]


def supported_pieces(building, walls):
    """Open pieces whose bounding shell walls actually open (consistent with LoD2)."""
    quads = np.asarray(walls).reshape(-1, 4, 3) if len(walls) else ()
    result = []
    for piece, top in pieces(building):
        single = _with_pieces(building, [(piece, top)])
        if any(edge_plans(q, single) for q in quads):
            result.append((piece, top))
    return result


class _with_pieces:
    def __init__(self, building, open_pieces):
        self.base, self.top, self.footprint = building.base, building.top, building.footprint
        self._pieces = open_pieces


def interior_quads(building, walls):
    """Downward soffits and inward-facing closing walls, in engine coordinates."""
    from turku3d.wall_surfaces import trapezoids

    footprint_edge = building.footprint.boundary
    quads = []
    for piece, top in supported_pieces(building, walls):
        for q in trapezoids(piece):
            points = np.c_[q[:, 0], np.full(4, top), q[:, 1]]
            quads.append(_oriented(points, np.array([0, -1.0, 0])))
        for polygon in shapely.get_parts(piece):
            for ring in [polygon.exterior, *polygon.interiors]:
                coords = np.asarray(ring.coords)
                for p, q in zip(coords[:-1], coords[1:], strict=True):
                    if np.linalg.norm(q - p) < 0.3:
                        continue
                    middle = (p + q) / 2
                    if footprint_edge.distance(shapely.Point(middle)) < 0.3:
                        continue  # A shell wall already owns this edge; it is cut instead.
                    direction = (q - p) / np.linalg.norm(q - p)
                    side = np.array([-direction[1], direction[0]])
                    if not polygon.contains(shapely.Point(middle + 0.2 * side)):
                        side = -side
                    points = np.array(
                        [[p[0], building.base, p[1]], [q[0], building.base, q[1]],
                         [q[0], top, q[1]], [p[0], top, p[1]]]
                    )
                    quads.append(_oriented(points, np.array([side[0], 0, side[1]])))
    return quads


def interior_meshes(building, walls, origin):
    from turku3d.facade_component_geometry import TRI
    from turku3d.package.gltf import GlbMesh

    quads = interior_quads(building, walls)
    if not quads:
        return []
    return [
        GlbMesh(
            f"open_ground_{building.key}",
            np.concatenate(quads) - origin,
            np.concatenate([TRI + 4 * i for i in range(len(quads))]),
            color=SOFFIT,
            roughness=0.9,
            extras=dict(
                layer="buildings",
                object_id=f"open_ground_{building.key}",
                surface="facade-component",
                building_id=building.facade_key,
                material_key="component_soffit",
            ),
        )
    ]
