"""Private outdoor access inherited by untagged paths inside mapped enclosures."""

import shapely
from shapely.ops import nearest_points

ENCLOSURE_GAP = 8.0  # routing closure only; never creates player collision
ENCLOSURES = {"fence", "wall", "city_wall", "retaining_wall", "hedge"}
RESIDENTIAL = {"house", "apartments", "residential", "detached", "terrace", "semidetached_house"}


def polygon(geometry):
    geometry = shapely.make_valid(shapely.force_2d(geometry))
    if geometry.geom_type == "LineString" and geometry.is_ring:
        geometry = shapely.make_valid(shapely.Polygon(geometry))
    return geometry if geometry.geom_type in ("Polygon", "MultiPolygon") else shapely.Polygon()


def private_areas(records):
    """Explicit private areas plus enclosed yards with positive privacy evidence.

    Small mapped garden beds can carry access=private while their surrounding
    paths do not. Walls, hedges and buildings identify the containing yard.
    Gate openings are sealed for this routing test only; players retain entry.
    """
    seeds, public, barriers, buildings, residential, gates = [], [], [], [], [], []
    for geometry, tags in records:
        area = polygon(geometry)
        if not area.is_empty:
            if tags.get("access") in ("private", "no") or (
                tags.get("leisure") == "garden"
                and tags.get("garden:type") in ("residential", "private")
            ):
                seeds.append(area)
            if tags.get("access") in ("yes", "public", "permissive") or tags.get("leisure") == "park":
                public.append(area)
            if tags.get("building") not in (None, "no"):
                buildings.append(area)
                if tags.get("building") in RESIDENTIAL:
                    residential.append(area)
        kind = tags.get("barrier")
        if kind in ENCLOSURES:
            barriers.append(area.boundary if not area.is_empty else shapely.force_2d(geometry))
        elif kind == "gate" and tags.get("access") in ("private", "no"):
            gates.append(shapely.force_2d(geometry))
    building = shapely.union_all(buildings)
    closures, endpoints = [], []
    for line in shapely.get_parts(shapely.union_all(barriers)):
        if line.geom_type != "LineString":
            continue
        for point in (shapely.Point(line.coords[0]), shapely.Point(line.coords[-1])):
            endpoints.append(point)
            if not building.is_empty and 0 < point.distance(building) <= ENCLOSURE_GAP:
                closures.append(shapely.LineString(nearest_points(point, building)))
    if endpoints:
        a, b = shapely.STRtree(endpoints).query(endpoints, predicate="dwithin", distance=3.0)
        for i, j in zip(a[a < b], b[a < b], strict=True):
            if endpoints[i].distance(endpoints[j]) > 0.01:
                closures.append(shapely.LineString([endpoints[i], endpoints[j]]))
    if buildings:
        a, b = shapely.STRtree(buildings).query(buildings, predicate="dwithin", distance=ENCLOSURE_GAP)
        for i, j in zip(a[a < b], b[a < b], strict=True):
            if buildings[i].distance(buildings[j]) > 0:
                closures.append(shapely.LineString(nearest_points(buildings[i], buildings[j])))
    # Thin bands node sub-centimetre endpoint/projection roundoff reliably.
    solid = shapely.union_all([building, *[g.buffer(0.1) for g in barriers+closures]])
    public_area = shapely.union_all(public)
    homes = shapely.union_all(residential)
    private = list(seeds)
    for component in shapely.get_parts(solid):
        if component.geom_type != "Polygon":
            continue
        for ring in component.interiors:
            cell = shapely.Polygon(ring)
            if not 20 <= cell.area <= 20000 or public_area.covers(cell.representative_point()):
                continue
            if (any(cell.covers(seed.representative_point()) for seed in seeds)
                    or any(cell.distance(gate) < 0.3 for gate in gates)
                    or (not homes.is_empty and cell.distance(homes) < 0.2)):
                private.append(shapely.make_valid(cell.buffer(0.1)))
    return shapely.difference(shapely.union_all(private, grid_size=0.01), building, grid_size=0.01)
