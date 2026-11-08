"""Measured soffits of open ground floors from classified lidar.

Under a raised building part with open ground beneath (OSM building:min_level / min_height, register.open_ground),
oblique pulses hit the facade down to the soffit and pass under it onto the ground behind. The lower edge of the
facade returns along the piece's open outline is the soffit; returns at ground level under the footprint confirm that
the ground floor is open. Turku's Köysirata: OSM's storey prior put the soffit 3.0 m over the median ground, the
facade returns along its 255 m start 3.2 m over it. Where the lidar is silent (no facade or ground returns), or its
edge contradicts an open ground floor, the OSM height stays (min_height, else the storey prior). The lidar refines the
storey OSM names, never moves the soffit to another one: an edge more than STOREY_BAND from the OSM height is another
facade's (a taller neighbour behind a bridge part, a canopy edge), as 7 of the first 20 raised parts measured in Turku
showed (-8.9 to +15 m off the tags, against 13 within 1.4 m). Panorama checks of the wider band: edges within 0.3 m
of the prior were right (Köysirata, an apartment corner on pilotis); a 1.3 m low edge under a pilotis block (E 23459846
N 6704419) was wrong (3 m in the panorama), so the band is a quarter of a storey.
"""

from __future__ import annotations

import numpy as np
import shapely

BAND = 0.6  # m outside the open outline: facade returns of oblique pulses
EDGE_REACH = 0.3  # m: an open piece's outline this close to the footprint's is an open facade
BIN = 0.2  # m height bins of the facade returns
MIN_FACADE = 40  # facade returns along the open outline needed for a measurement
MIN_UNDER = 10  # returns at ground level under the open piece needed to call it open
EDGE_SHARE = 0.25  # the soffit's bin holds at least this share of the median facade bin above it
GROUND_BAND = 1.0  # m over the local ground: an under-footprint return at ground level
NOT_FACADE = (2, 3, 4, 5, 7, 9, 18)  # ground, vegetation, noise and water returns
STOREY_BAND = 0.75  # m: a measured soffit further than this from the OSM height keeps the OSM height


def open_edges(piece, footprint):
    """The stretches of an open piece's outline on the building's own outline (its open facades)."""
    edges = shapely.intersection(piece.boundary, footprint.boundary.buffer(EDGE_REACH))
    return shapely.line_merge(edges) if not edges.is_empty else edges


def lower_edge(heights, floor, ceiling):
    """The lowest height from which the facade returns run on densely, or None: the first BIN whose count and the
    next bin's both reach EDGE_SHARE of the median count of the bins above it (columns, lamps and people under the
    soffit are sparse; the facade above it is dense)."""
    heights = np.asarray(heights, float)
    heights = heights[(heights >= floor) & (heights < ceiling)]
    if len(heights) < MIN_FACADE:
        return None
    counts, edges = np.histogram(heights, bins=np.arange(floor, ceiling + BIN, BIN))
    for i in range(len(counts) - 1):
        above = counts[i:]
        need = max(3.0, EDGE_SHARE * float(np.median(above[above > 0])))
        if counts[i] >= need and counts[i + 1] >= need:
            return float(edges[i])
    return None


def measure(piece, footprint, top, points, classes):
    """{soffit, ground, facade, under} for one open piece (absolute engine heights), or None without evidence.
    `points` (n, 3) engine x, y, z and `classes` (n,) are the classified returns around the building."""
    points, classes = np.asarray(points, float), np.asarray(classes)
    edges = open_edges(piece, footprint)
    if edges.is_empty or edges.length < 4.0:
        return None
    ground = classes == 2
    near = shapely.contains_xy(piece.buffer(2.0), points[:, 0], points[:, 2]) & ground
    if near.sum() < 3:
        return None
    level = float(np.median(points[near, 1]))
    band = edges.buffer(BAND, cap_style="flat").difference(footprint)
    facade = shapely.contains_xy(band, points[:, 0], points[:, 2]) & ~np.isin(classes, NOT_FACADE)
    from turku3d.open_ground import MIN_OPEN_HEIGHT, MIN_VOLUME_ABOVE

    floor = float(np.quantile(points[near, 1], 0.1)) + MIN_OPEN_HEIGHT - 0.3
    soffit = lower_edge(points[facade, 1], floor, top - MIN_VOLUME_ABOVE + BIN)
    inner = piece.buffer(-0.5)
    under = int(
        (shapely.contains_xy(inner, points[:, 0], points[:, 2]) & (points[:, 1] < level + GROUND_BAND)).sum()
    ) if not inner.is_empty else 0
    if soffit is None or under < MIN_UNDER:
        return None
    return dict(soffit=round(soffit, 2), ground=round(level, 2), facade=int(facade.sum()), under=under)


def prepare(tile, osm, vector, paths):
    """Measured soffits of the tile's own buildings' open pieces: [(building key, piece, record)]."""
    from turku3d import config as C
    from turku3d.lidar import TileStore

    from .inputs import buildings as read_buildings
    from .register import read_osm

    if osm is None or vector is None or not paths:
        return []
    raised = read_osm(tile, osm).get("open_ground", [])
    if not raised:
        return []
    survey = {p.stem: p for p in paths}
    store = TileStore(source_paths=survey)
    records = []
    for building in read_buildings(vector, tile):
        pieces = [
            piece
            for polygon, _ in raised
            if building.footprint.intersects(polygon)
            and (piece := shapely.make_valid(building.footprint.intersection(polygon))).area >= 4
        ]
        if not pieces:
            continue
        bounds = C.BBoxLocal(*building.footprint.buffer(BAND + 2.5).bounds)
        if not all(b.name in survey for b in C.laz_tiles_for_bbox(bounds)):
            continue
        cloud = store.query(bounds)
        for piece in pieces:
            found = measure(piece, building.footprint, building.top, cloud.points, cloud.records["classification"])
            if found is not None:
                records.append((building.key, piece, found))
    return records


def apply(building, records):
    """Set each open piece's lower edge from its measured soffit (register.open_ground_levels' ground)."""
    from turku3d.open_ground import MIN_OPEN_HEIGHT, MIN_VOLUME_ABOVE

    pieces = getattr(building, "open_ground", None)
    if not pieces:
        return
    own = [(piece, r) for key, piece, r in records if key == building.key]
    if not own:
        return
    result = []
    for piece, low, *ground in pieces:
        level = ground[0] if ground else building.base
        match = max(own, key=lambda item: item[0].intersection(piece).area)
        overlap = match[0].intersection(piece).area
        soffit = match[1]["soffit"]
        if (
            overlap >= 0.5 * piece.area
            and soffit - level >= MIN_OPEN_HEIGHT
            and building.top - soffit >= MIN_VOLUME_ABOVE
            and abs(soffit - level - low) <= STOREY_BAND
        ):
            low = soffit - level
        result.append((piece, low, *ground))
    building.open_ground = result
