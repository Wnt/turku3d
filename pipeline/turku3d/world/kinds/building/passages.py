"""Building passages and arcades: where a way runs through or along a building's ground storey, it is open.

Sources (`facts`, in `building_shell.discover`): the open map's ways at ground level under a roof
(`tunnel=building_passage|covered`, or `covered=yes|arcade|colonnade` without a tunnel) that run inside the footprint,
as the old game's corridor (coarse/tunnels.py: `dimensions`, `passage_mouths`), widened by the street register's
part (Katualueet) that the way runs in where that part reaches under the building: the register draws the footway
of an arcade to its back wall. LoD2 shells close the ground storey; this module opens it. The result is the shell's
`passages`: the open area inside the footprint, its clear height when the map gives one, and foot or road use.

Geometry (`cuts`, `interior`, used by compile and facade.build): a facade whose base line runs in a passage opens
there from the ground up to the soffit.
- An ARCADE: the open stretch's far side lies inside the building. The ground storey there is a recess of the
  wall: its openings sit on the back wall, square piers stand on the facade line at the old rhythm (at most 4 m
  apart, the old facade kit's arcade prior).
- A THROUGH MOUTH: the far side is the outline again (a passage through the building, or an arcade's open end).
  The wall is cut.
A soffit covers the whole passage and lining walls close it where no facade's back wall does. Collision is the
drawn mesh, so a player walks (and on a road passage drives) through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from . import mesh

PATHS = ("footway", "path", "cycleway", "pedestrian", "steps", "corridor", "bridleway")
FOOT_CLEAR_M = 3.0  # soffit over the floor of a footway arcade or passage: the arcades of the stone town
ROAD_CLEAR_M = 4.2  # a drive-through: a delivery lorry
SERVICE_WIDTH_M = 3.5  # a service way's gateway through a building when the map gives no lanes or width
MIN_CLEAR_M = 2.3
ABOVE_M = 2.5  # at least one storey stays above the soffit, or the building is no roof over a passage
MIN_INSIDE_M = 1.0  # a way that runs this little inside the footprint passes beside the building, not under it
MIN_AREA_M2 = 2.0
REGISTER_REACH_M = 6.0  # how far from the way a register part under the building still belongs to its passage
REGISTER_USES = ("jalkakäytävä", "yhdistetty kevyen liikenteen väylä", "pyörätie", "ajorata", "muu liikennealue",
                 "aukio", "kävelykatu", "pihakatu", "portaat")
MIN_OPEN_M = 1.2  # shorter open stretches of a facade are left closed
MIN_DEPTH_M = 1.0  # a shallower recess behind a facade is left closed
PIER_W = 0.5  # square pier, on the facade line
PIER_SPACING_M = 4.0  # the old arcade prior: count = length / 4 + 1
ARCADE_MIN_M = 5.0  # an open stretch this long with a back wall is an arcade with piers; shorter ones are a niche
ARCADE_MAX_DEPTH_M = 6.0  # deeper, or a stretch shorter than 1.5 times its depth, is a passage into the building
LINING_SHADE = 0.75  # little sky reaches the lining walls
SOFFIT = {"family": "plaster", "colour": "#c4c1ba", "finish": "smooth"}


def dimensions(tags: dict) -> tuple[float, float | None, bool]:
    """(width, clear height from the map or None, road) of a way: the old game's conservative defaults."""
    path = tags.get("highway") in PATHS
    try:
        lanes = float(tags["lanes"])
    except (KeyError, TypeError, ValueError):
        lanes = None
    if path:
        width = 3.0
    elif lanes is None and tags.get("highway") == "service":
        width = SERVICE_WIDTH_M  # a gateway into a yard is one lane, not the old tunnel box of two
    else:
        width = max(4.0, (lanes or 2.0) * 3.5 + 1.0)
    try:
        width = float(np.clip(float(str(tags.get("width", width)).removesuffix(" m")), 1.5, 30.0))
    except ValueError:
        pass
    clear = None
    try:
        clear = float(np.clip(float(str(tags["maxheight"]).removesuffix(" m")), MIN_CLEAR_M, 8.0))
    except (KeyError, ValueError):
        pass
    return width, clear, not path


def at_ground_under_roof(tags: dict) -> bool:
    """A way at ground level that the map says runs under a building or a roof."""
    try:
        layer = float(tags.get("layer", 0))
    except ValueError:
        layer = 0.0
    if layer != 0 or tags.get("indoor") not in (None, "no") or tags.get("level") not in (None, "0"):
        return False
    if not tags.get("highway"):
        return False
    tunnel = tags.get("tunnel", "no")
    if tunnel in ("building_passage", "covered"):
        return True
    return tunnel == "no" and tags.get("covered") in ("yes", "arcade", "colonnade")


def mouths(line: LineString, width: float):
    """The clear corridor of a way carried on past both ends by half its width (coarse/tunnels.passage_mouths): a
    way ends at the node where the mapper put the facade, which may lie a metre off the surveyed outline."""
    coords = np.asarray(shapely.get_coordinates(line))
    ends = []
    for end, inner in ((coords[0], coords[1]), (coords[-1], coords[-2])):
        step = end - inner
        length = np.linalg.norm(step)
        ends.append(end + step / length * width / 2 if length > 1e-9 else end)
    extended = LineString([ends[0], *coords[1:-1], ends[1]])
    return extended.buffer(width / 2, cap_style="flat", join_style="mitre")


def ways_and_parts(sources, box) -> tuple[list, list]:
    """(line, tags, ref) of the map's ways under a roof and (polygon, key) of the register's street parts in box."""
    ways, parts = [], []
    try:
        frame = sources.osm.features(box)
    except Exception:  # no map table (offline): no passages are known
        return ways, parts
    if frame is None or not len(frame):
        return ways, parts
    lines = frame[frame.geom_type == "LineString"]
    for row in lines[lines["highway"].notna()].itertuples():
        try:
            tags = json.loads(row.tags) if isinstance(row.tags, str) else {}
        except ValueError:
            continue
        if at_ground_under_roof(tags):
            ways.append((row.geometry, tags, f"osm:{row.osm_type}/{row.osm_id}"))
    if not ways:
        return ways, parts
    try:
        from ...street import registers

        streets = registers.layer("GIS:Katualueet", box)
    except Exception:  # no register (offline): the map's corridors alone
        return ways, parts
    for row in streets.itertuples():
        use = str(getattr(row, "Katuosalaji", "") or "").lower()
        if row.geometry.geom_type == "Polygon" and use in REGISTER_USES:
            parts.append((shapely.make_valid(shapely.force_2d(row.geometry)), f"street:{row.feature_key}"))
    return ways, parts


def facts(outline, ways: list, parts: list) -> list[dict]:
    """The shell's `passages` of one footprint (survey frame): the open ground-storey areas, merged per use."""
    found = []
    for line, tags, ref in ways:
        if line.intersection(outline).length < MIN_INSIDE_M:
            continue
        width, clear, road = dimensions(tags)
        area = mouths(line, width).intersection(outline)
        refs = [ref]
        for polygon, key in parts:
            if line.intersection(polygon).length < MIN_INSIDE_M:
                continue
            under = polygon.intersection(outline).intersection(line.buffer(REGISTER_REACH_M))
            if under.area >= 1.0:
                area = area.union(under)
                refs.append(key)
        area = shapely.make_valid(area.buffer(0.05, join_style="mitre").buffer(-0.05, join_style="mitre"))
        if area.area >= MIN_AREA_M2:
            found.append({"area": area, "clear_m": clear, "use": "road" if road else "foot", "refs": refs})
    merged = []
    for use in ("road", "foot"):
        group = [f for f in found if f["use"] == use]
        while group:
            first = group.pop(0)
            joined = True
            while joined:
                joined = False
                for other in list(group):
                    if first["area"].buffer(0.05).intersects(other["area"]):
                        first = {"area": first["area"].union(other["area"]), "use": use,
                                 "clear_m": max([c for c in (first["clear_m"], other["clear_m"]) if c] or [None]),
                                 "refs": first["refs"] + [r for r in other["refs"] if r not in first["refs"]]}
                        group.remove(other)
                        joined = True
            merged.append(first)
    out = []
    for item in merged:
        rings = []
        for polygon in shapely.get_parts(item["area"]):
            if polygon.geom_type == "Polygon" and polygon.area >= MIN_AREA_M2:
                polygon = shapely.geometry.polygon.orient(polygon.simplify(0.02), 1.0)
                rings.append([np.round(np.asarray(r.coords)[:-1], 3).tolist() for r in (polygon.exterior, *polygon.interiors)])
        if rings:
            out.append({"polygons": rings, "clear_m": item["clear_m"], "use": item["use"], "refs": sorted(set(item["refs"]))})
    return out


# ---- compile side (survey frame relative to the building's origin) ----


@dataclass
class Passage:
    area: object  # shapely (Multi)Polygon, E N
    floor_h: float  # the ground at the passage
    soffit_h: float
    use: str


@dataclass
class Cut:
    """An open stretch of one facade: u from a to b, open from below the ground to `ceiling` (facade v)."""

    a: float
    b: float
    ceiling: float
    depth: float | None  # the back wall's distance behind the facade line; None for a through mouth
    passage: Passage
    piers: list = field(default_factory=list)  # u of each pier on the facade line
    open_ends: tuple = (False, False)  # the passage carries on past a / past b (no cheek there)


def localise(passages: list, origin: np.ndarray) -> list:
    out = []
    for item in passages:
        moved = [[(np.asarray(r, float) - origin[:2]).tolist() for r in polygon] for polygon in item["polygons"]]
        out.append({**item, "polygons": moved})
    return out


def read(doc: dict, shell, ground_storey_top: float | None) -> list[Passage]:
    """The description's passages with a floor and a soffit height. `ground_storey_top`: the first floor's level
    over the ground where the building's storeys say so; the soffit stays under it."""
    terrain = [np.asarray(line, float) for line in doc["shell"].get("terrain", []) if len(line)]
    points = np.vstack(terrain) if terrain else np.zeros((0, 3))
    out = []
    for item in doc["shell"].get("passages", []):
        polygons = [Polygon(rings[0], rings[1:]) for rings in item.get("polygons", []) if len(rings[0]) >= 3]
        area = shapely.make_valid(shapely.unary_union([p.buffer(0) for p in polygons])) if polygons else None
        if area is None or area.is_empty or area.area < MIN_AREA_M2:
            continue
        near = points[shapely.dwithin(shapely.points(points[:, :2]), area, 1.0)] if len(points) else points
        floor = float(near[:, 2].max()) if len(near) else shell.base_h
        clear = item.get("clear_m") or (ROAD_CLEAR_M if item.get("use") == "road" else FOOT_CLEAR_M)
        if not item.get("clear_m") and ground_storey_top:
            clear = min(clear, max(ground_storey_top - 0.3, MIN_CLEAR_M))
        if shell.top_h - (floor + clear) < ABOVE_M:
            continue
        out.append(Passage(area, floor, floor + clear, item.get("use", "foot")))
    return out


def cuts(shell, passages: list[Passage]) -> dict:
    """{facade index: [Cut]}: where each grounded facade's base line runs in a passage."""
    if not passages:
        return {}
    outline = shell.footprint.boundary
    found = {}
    for facade in shell.facades:
        if not facade.grounded or facade.width < MIN_OPEN_M:
            continue
        us = np.arange(0.125, facade.width, 0.25)
        inward = facade.point(us, 0.0, -0.3)[:, :2]
        # Only where the wall itself stands on the ground: a facade grouped with a set-back upper wall on the same
        # line has no ground storey there.
        ground = np.array([facade.ground_at(u) for u in us]) if len(facade.ground) else np.zeros(len(us))
        standing = shapely.contains_xy(facade.outline, us, np.maximum(ground, 0.0) + 0.3)
        for passage in passages:
            ceiling = passage.soffit_h - float(facade.origin[2])
            if ceiling < MIN_CLEAR_M:
                continue
            inside = shapely.contains_xy(passage.area, inward[:, 0], inward[:, 1]) & standing
            edges = np.flatnonzero(np.diff(np.r_[0, inside.astype(int), 0]))
            for first, last in zip(edges[::2], edges[1::2], strict=True):
                a = 0.0 if first == 0 else float(us[first] - 0.125)
                b = facade.width if last >= len(us) else float(us[last - 1] + 0.125)
                if b - a < MIN_OPEN_M:
                    continue
                lengths, through = [], 0
                for u in np.linspace(a + 0.1 * (b - a), b - 0.1 * (b - a), 5):
                    start = facade.point(u, 0.0, -0.05)[:2]
                    probe = LineString([start, facade.point(u, 0.0, -200.0)[:2]])
                    piece = next((g for g in shapely.get_parts(passage.area.intersection(probe))
                                  if g.geom_type == "LineString" and g.distance(Point(start)) < 0.3), None)
                    if piece is None or piece.length < 0.3:
                        continue
                    end = max((Point(c) for c in piece.coords), key=lambda c: c.distance(Point(start)))
                    lengths.append(piece.length)
                    through += outline.distance(end) < 0.3
                if not lengths:
                    continue
                depth = None if through > len(lengths) / 2 else float(np.median(lengths))
                if depth is not None and depth < MIN_DEPTH_M:
                    continue  # a register edge a little inside the wall, not a recess anybody walks into
                if depth is not None and (depth > ARCADE_MAX_DEPTH_M or b - a < 1.5 * depth):
                    depth = None  # a passage that ends inside the building: open mouth, the lining closes its end
                ends = []
                for u in (a - 0.3, b + 0.3):
                    probe = facade.point(np.clip(u, -0.3, facade.width + 0.3), 0.0, -min(depth or 1.0, 1.0) / 2)[:2]
                    ends.append(bool(passage.area.buffer(0.1).contains(Point(probe))) or not 0.0 < u < facade.width)
                cut = Cut(a, b, ceiling, depth, passage, open_ends=tuple(ends))
                if depth is not None and b - a >= ARCADE_MIN_M:
                    count = max(2, int((b - a) / PIER_SPACING_M) + 1)
                    piers = np.linspace(a + PIER_W / 2, b - PIER_W / 2, count)
                    # Where the wall carries on past an end of the arcade it is the support there: no pier against it.
                    if not ends[0]:
                        piers = piers[1:]
                    if not ends[1]:
                        piers = piers[:-1]
                    cut.piers = [float(u) for u in piers]
                found.setdefault(facade.index, []).append(cut)
    return found


def strip(facade, cut: Cut) -> Polygon:
    """The plan of an arcade's recess: between the facade line and its back wall over the cut."""
    corners = [facade.point(cut.a, 0.0, 0.0), facade.point(cut.b, 0.0, 0.0),
               facade.point(cut.b, 0.0, -cut.depth), facade.point(cut.a, 0.0, -cut.depth)]
    return Polygon([c[:2] for c in corners])


def interior(soup: mesh.Soup, shell, passages: list[Passage], found: dict, palette, wall_key: str) -> None:
    """The soffit over every passage and the lining walls round it where no facade draws a back wall."""
    if not passages:
        return
    by_index = {f.index: f for f in shell.facades}
    soffit_key = palette.key(SOFFIT)
    lining_key = palette.occluded(wall_key, LINING_SHADE)
    outline = shell.footprint.boundary
    for passage in passages:
        area = passage.area
        backs = []
        for index, items in found.items():
            facade = by_index[index]
            for cut in items:
                if cut.passage is not passage or cut.depth is None:
                    continue
                # The recess is exactly the strip behind the facade: what the passage held behind it within a metre
                # beyond the back wall becomes the strip, so the soffit and the lining meet the facade's back wall.
                behind = Polygon([c[:2] for c in (facade.point(cut.a, 0.0, 0.05), facade.point(cut.b, 0.0, 0.05),
                                                     facade.point(cut.b, 0.0, -cut.depth - 1.0),
                                                     facade.point(cut.a, 0.0, -cut.depth - 1.0))])
                area = area.difference(behind).union(strip(facade, cut))
                backs.append(LineString([facade.point(cut.a, 0.0, -cut.depth)[:2], facade.point(cut.b, 0.0, -cut.depth)[:2]]))
        area = shapely.make_valid(area.buffer(0.02, join_style="mitre").buffer(-0.02, join_style="mitre"))
        low = passage.floor_h - 0.6
        for polygon in shapely.get_parts(area):
            if polygon.geom_type != "Polygon" or polygon.area < 0.2:
                continue
            polygon = shapely.geometry.polygon.orient(polygon, 1.0)
            vertices, triangles = mesh.triangulate(polygon)
            if len(triangles):
                positions = np.column_stack([vertices, np.full(len(vertices), passage.soffit_h)])
                soup.add("walls", soffit_key, positions, triangles[:, ::-1], vertices,
                         np.tile([0.0, 0.0, -1.0], (len(vertices), 1)))
            for ring in (polygon.exterior, *polygon.interiors):
                coords = np.asarray(ring.coords)
                for p, q in zip(coords[:-1], coords[1:], strict=True):
                    length = float(np.linalg.norm(q - p))
                    middle = Point((p + q) / 2)
                    if length < 0.05 or outline.distance(middle) < 0.15:
                        continue  # the facade owns this edge: it is cut or carries the arcade's piers
                    if any(back.distance(middle) < 0.05 for back in backs):
                        continue  # a facade draws this back wall, with its shopfronts and doors
                    # The polygon lies to the left of each edge: the lining faces left, its u runs from q to p.
                    u_dir = np.array([*(p - q) / length, 0.0])
                    frame = SimpleNamespace(origin=np.array([q[0], q[1], low]), u_dir=u_dir,
                                            normal=np.array([u_dir[1], -u_dir[0], 0.0]))
                    local = mesh.Soup()
                    mesh.face(local, "walls", lining_key, Polygon([(0, 0), (length, 0), (length, passage.soffit_h - low),
                                                                   (0, passage.soffit_h - low)]))
                    soup.place(local, frame)
