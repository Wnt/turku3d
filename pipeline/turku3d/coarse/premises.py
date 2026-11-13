"""Customer premises in the town: restaurants, cafés, bars, banks and pharmacies open their own walls as shopfronts.

The open map places such premises as points (or tags the building outline). Proven only gave
them a door; in the town (``coarse.milieus.in_town``, Jonni's area scope of 2026-11-13) their
building's street storey is a shopfront where the premise opens, as a mapped shop already gets
one. Two rules keep it on the right wall of the right building (W2 653227c9, after Jonni caught a
restaurant on the wrong street):

- A premise belongs to one building: the footprint holding the point (where two do, the one on
  the point's ``addr:street``, else the smaller); a point outside every footprint (mapped on the
  wall line or at the door) to the nearest building within ``REACH_M`` that stands on the point's
  street, and never one a street centreline separates from the point.
- Its front is the open wall nearest the point (and, on a corner, the other open wall within a
  room's depth): a wall line (``wall_lines``) at least ``MIN_FRONT_M`` long, at most half of it
  against a neighbour's footprint (a party wall, where nothing opens).

Owners are decided over the tile's buildings and its wall context (the neighbours' complete
buildings), so a building on a tile edge gets the same answer in every tile. The fronts are
footprint edges in the engine's x/z frame (``premise_fronts``); ``facade_programs`` opens the
ground storey of the walls on them, below every description, reading and reviewed patch.
"""

from __future__ import annotations

AMENITIES = frozenset({"restaurant", "cafe", "bar", "pub", "bank", "pharmacy"})
REACH_M = 1.5  # a premise mapped on the wall line or at the door, outside the footprint
PARTY_GAP_M = 0.5  # a neighbour's footprint this close to a wall stands against it
MIN_FRONT_M = 4.0
FRONT_REACH_M = (14.0, 8.0)  # the nearest open wall; another (a corner) within a room's depth
BEND_DEG = 12.0  # edges turning less than this are one wall line (a facade traced in pieces)
STREET_REACH_M = 15.0  # streets a building stands on, for matching a premise's addr:street


def premise(geom, tags):
    """(point, amenity, street or "") of a map feature that is a customer premise, else None."""
    amenity = tags.get("amenity")
    if amenity not in AMENITIES:
        return None
    point = geom if geom.geom_type == "Point" else geom.representative_point()
    return point, amenity, str(tags.get("addr:street") or "").strip().casefold()


def street_of(address):
    """The street of an address ("Läntinen Rantakatu 27" -> "läntinen rantakatu")."""
    import re

    return re.sub(r"\s+\d.*$", "", address or "").strip().casefold()


def _streets_of(footprint, streets, addresses):
    """Casefolded names of the streets a building stands on: the address of the map outline that is
    this building, and the named centrelines near it."""
    names = {
        name
        for polygon, name in addresses
        if footprint.intersects(polygon)
        and footprint.intersection(polygon).area > 0.3 * min(footprint.area, polygon.area)
    }
    names |= {
        name.casefold() for line, name, _ in streets if name and footprint.distance(line) <= STREET_REACH_M
    }
    return names


def _across(point, footprint, streets):
    """Whether a street centreline runs between the point and the footprint."""
    import shapely

    gap = shapely.shortest_line(point, footprint)
    return any(gap.intersects(line) for line, _, _ in streets)


def owners(points, buildings, streets=(), addresses=()):
    """{building index: [(point, amenity)]} for premises ``(point, amenity, street)`` over footprints."""
    import shapely

    result = {}
    if not points or not buildings:
        return result
    footprints = [b.footprint for b in buildings]
    tree = shapely.STRtree(footprints)
    names = {}

    def on(j, street):
        if j not in names:
            names[j] = _streets_of(footprints[j], streets, addresses)
        return street in names[j]

    for point, amenity, street in points:
        holding = [int(j) for j in tree.query(point, predicate="within")]
        if holding:
            pick = min(holding, key=lambda j: (bool(street) and not on(j, street), footprints[j].area))
        else:
            near = [
                int(j)
                for j in tree.query(point, predicate="dwithin", distance=REACH_M)
                if not _across(point, footprints[int(j)], streets)
            ]
            if street:
                near = [j for j in near if on(j, street)]
            if not near:
                continue
            pick = min(near, key=lambda j: (footprints[j].distance(point), footprints[j].area))
        result.setdefault(pick, []).append((point, amenity))
    return result


def wall_lines(polygon):
    """The exterior ring's edges grouped into wall lines: consecutive edges turning less than
    ``BEND_DEG`` (a facade traced in several short pieces) form one line. Lists of (a, b) edges."""
    import math

    coords = list(polygon.exterior.coords)[:-1]
    edges = [(coords[k], coords[(k + 1) % len(coords)]) for k in range(len(coords))]
    edges = [(a, b) for a, b in edges if math.dist(a, b) > 1e-6]

    def heading(edge):
        (x0, z0), (x1, z1) = edge[0][:2], edge[1][:2]
        return math.atan2(z1 - z0, x1 - x0)

    def straight(e, f):
        turn = abs((heading(f) - heading(e) + math.pi) % (2 * math.pi) - math.pi)
        return turn < math.radians(BEND_DEG)

    if not edges:
        return []
    # Start at a corner so that no line wraps over the ring's first vertex.
    start = next((k for k in range(len(edges)) if not straight(edges[k - 1], edges[k])), 0)
    edges = edges[start:] + edges[:start]
    lines = [[edges[0]]]
    for edge in edges[1:]:
        if straight(lines[-1][-1], edge):
            lines[-1].append(edge)
        else:
            lines.append([edge])
    return lines


def fronts(footprint, points, neighbours):
    """Footprint edges ``(x0, z0, x1, z1)`` that open as the premises' fronts: the edges of the open wall
    line nearest each premise (and of another open line within a room's depth, on a corner)."""
    import shapely

    against = shapely.union_all([n for n in neighbours if n.distance(footprint) <= PARTY_GAP_M])
    against = against.buffer(PARTY_GAP_M) if not against.is_empty else against
    open_lines = []
    for polygon in getattr(footprint, "geoms", [footprint]):
        for line in wall_lines(polygon):
            shape = shapely.MultiLineString([[a[:2], b[:2]] for a, b in line])
            if shape.length < MIN_FRONT_M:
                continue
            party = shape.intersection(against).length if not against.is_empty else 0.0
            if party <= 0.5 * shape.length:
                open_lines.append((shape, line))
    chosen = {}
    for point in points:
        gaps = sorted((shape.distance(point), k) for k, (shape, _) in enumerate(open_lines))
        for rank, (gap, k) in enumerate(gaps):
            if gap <= FRONT_REACH_M[0 if rank == 0 else 1]:
                chosen[k] = open_lines[k][1]
    return [
        [round(float(v), 2) for v in (*a[:2], *b[:2])]
        for _, line in sorted(chosen.items())
        for a, b in line
    ]


def apply(buildings, context, appearance):
    """Give each town building of the tile its premises' fronts (``premise_fronts``, ``premise_uses``)."""
    from .milieus import in_town

    points = appearance.get("premises") or []
    if not points or not any(in_town(b.attributes) for b in buildings):
        return
    every = [*buildings, *context]
    addresses = [
        (polygon, street_of(attrs["address"]))
        for polygon, attrs in appearance.get("buildings", ())
        if attrs.get("address")
    ]
    held = owners(points, every, appearance.get("streets", ()), addresses)
    for index, building in enumerate(buildings):
        mine = held.get(index)
        if not mine or not in_town(building.attributes):
            continue
        if building.attributes.get("ground_floor") == "shops":
            continue  # a mapped shop already opens the whole street storey
        neighbours = [other.footprint for other in every if other is not building]
        edges = fronts(building.footprint, [p for p, _ in mine], neighbours)
        if edges:
            building.attributes["premise_fronts"] = edges
            building.attributes["premise_uses"] = sorted({amenity for _, amenity in mine})
