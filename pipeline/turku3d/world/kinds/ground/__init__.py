"""The ground kind: one entity per 62.5 m cell, the whole ground surface of that cell.

`ground/c_<i>_<j>` is the lidar relief of the cell split into covers (asphalt, pavers, setts, grass, ...) by the
city's street-part and green-area polygons and by OpenStreetMap where the registers are silent, with kerb stones as
solids along the carriageways and crossings and lane lines as painted regions. Description schema:
docs/specs/ground-description.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import shapely

from ...api import Area, Output, Stub, envelope
from ...frame import CELL, cell_at, cell_box_en, cell_key
from ...street import lidar, osm, registers
from . import aerial, covers
from .mesh import compile_cell

VERSION = 1
STEP = 0.5            # relief node spacing: the lidar raster's own
HALO = 8              # relief nodes around the cell: what a kerb's two levels are carried from
MARGIN = HALO * STEP  # surfaces reach this far beyond the cell for the same reason
ROADS = ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'living_street',
         'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link')
# Which streets carry a centre line, and its rhythm: the old generator's marking rules (roads/paint.py).
CENTRE_LINED = ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'motorway_link', 'trunk_link', 'primary_link',
                'secondary_link', 'tertiary_link')
CENTRE_LINED_WHEN_LANES_TAGGED = ('residential', 'unclassified')
CROSSING_WIDTH = 4.0
JUNCTION_CLEAR = 10.0   # lane lines stop this far from where roads meet
DASH, GAP = 3.0, 5.0    # centre line in town
UNMAPPED_RANK = 1_000_000  # orthophoto outlines lie under everything mapped
CYCLE_LANE = 1.5        # width of a cycle lane on the carriageway when its tag says less or nothing
CYCLE_DASH, CYCLE_GAP = 1.0, 2.0
PARKING_LANE = 2.0      # width of a kerbside parking lane on the carriageway
PARKING_CLEAR = 0.4     # a parking bay's outline is painted where it lies this far inside the carriageway
SOLID = 1e6             # dash length of an unbroken line
SIDE_STREET = 9.0       # half the width a joining street's mouth takes from a lane
CORRIDOR = 14.0         # a street's carriageway lies within this distance of its mapped centre line


def _tags(row) -> dict:
    try:
        return json.loads(row.tags) if isinstance(row.tags, str) else {}
    except ValueError:
        return {}


def _text(row, name: str) -> str:
    """A register attribute as text; a missing one is empty."""
    value = getattr(row, name, None)
    return value if isinstance(value, str) else ''


def _lanes(row) -> int:
    try:
        return int(float(row.lanes))
    except (TypeError, ValueError):
        return 0


def _rings(polygon) -> list:
    """Rings at full precision: a neighbour cell clips the same polygon elsewhere, and both must cut the shared cell
    edge at the same point."""
    return [np.asarray(ring.coords)[:-1].tolist() for ring in (polygon.exterior, *polygon.interiors)]


def _polygons(shape) -> list:
    return [p for p in shapely.get_parts(shape) if p.geom_type == 'Polygon' and p.area > 0.01]


def _surfaces(box) -> list[tuple]:
    """(shape, row) of every polygon that says what the ground is, in the survey frame."""
    found = []
    streets = registers.layer('GIS:Katualueet', box)
    for row in streets.itertuples():
        if row.geometry.geom_type != 'Polygon':
            continue
        use = _text(row, 'Katuosalaji')
        level = 'low' if use.lower() in covers.LOW_USES else 'high'
        found.append((shapely.make_valid(shapely.force_2d(row.geometry)), {
            'key': f'street:{row.feature_key}', 'source': 'street', 'level': level, 'use': use,
            'surface': _text(row, 'Pintamateriaali'), 'rank': 0 if level == 'low' else 1}))
    greens = registers.layer('GIS:Viheralueet', box)
    for row in greens.itertuples():
        if row.geometry.geom_type != 'Polygon':
            continue
        found.append((shapely.make_valid(shapely.force_2d(row.geometry)), {
            'key': f'green:{row.feature_key}', 'source': 'green', 'level': 'high',
            'use': _text(row, 'Viheralueosalaji'), 'surface': _text(row, 'Pintamateriaali'),
            'rank': 2}))
    mapped = osm.features(box)
    if mapped is not None:
        areas = mapped[mapped.geom_type.isin(('Polygon', 'MultiPolygon')) & mapped.building.isna()]
        rows = []
        for row in areas.itertuples():
            tags = _tags(row)
            if tags.get('parking') in ('underground', 'multi-storey', 'rooftop') or tags.get('layer', '0') not in ('0', ''):
                continue
            if 'tunnel' in tags or 'indoor' in tags or 'highway' in tags:
                continue
            name = covers.of_osm(tags)
            if name:
                rows.append((shapely.make_valid(row.geometry), name, f'osm:{row.osm_type}/{row.osm_id}'))
        # Small areas lie inside large ones (a flowerbed in a lawn): the small one wins.
        for order, (shape, name, key) in enumerate(sorted(rows, key=lambda item: item[0].area)):
            # Open water is the bed under it: the skin drops below the water level there (the `water` kind draws the surface).
            # It wins over every other mapped area (a riverside park is drawn across the river), not over the registers
            # (a bridge deck stays a street).
            wet = name == 'water'
            found.append((shape, {'key': key, 'source': 'osm', 'level': 'bed' if wet else 'high', 'use': name,
                                  'surface': '', 'rank': 2.5 if wet else 3 + order}))
    return found


def _junctions(roads):
    """Where roads meet and lane markings stop: the surroundings of every node with three or more arms. A node where
    one way ends and the next begins (the mapper split the street where a tag changes) has two and is no junction."""
    arms: dict[tuple[float, float], int] = {}
    for row in roads.itertuples():
        points = [(round(x, 2), round(y, 2)) for x, y in row.geometry.coords]
        for index, point in enumerate(points):
            arms[point] = arms.get(point, 0) + (1 if index in (0, len(points) - 1) else 2)
    return shapely.unary_union([shapely.Point(p).buffer(JUNCTION_CLEAR) for p, count in arms.items() if count > 2]
                               or [shapely.Polygon()])


def _paint(box) -> tuple[list, list]:
    """Marked crossings as walking lines and centre-lined roads as (line, station of its start), survey frame."""
    mapped = osm.features(box)
    if mapped is None:
        return [], []
    lines = mapped[mapped.geom_type == 'LineString']
    roads = lines[lines.highway.isin(ROADS)]
    crossings = []
    ways = lines[lines.highway.isin(('footway', 'cycleway', 'path')) & lines.crossing.notna()]
    for row in ways.itertuples():
        tags = _tags(row)
        if tags.get('crossing') in ('unmarked', 'no') or tags.get('crossing:markings') == 'no':
            continue
        crossings.append((row.geometry, f'osm:way/{row.osm_id}'))
    walked = shapely.unary_union([line for line, _ in crossings]) if crossings else shapely.LineString()
    nodes = mapped[(mapped.geom_type == 'Point') & (mapped.highway == 'crossing')]
    for row in nodes.itertuples():
        tags = _tags(row)
        if tags.get('crossing') in ('unmarked', 'no') or tags.get('crossing:markings') == 'no':
            continue
        if row.geometry.distance(walked) < 2.0 or not len(roads):
            continue
        road = roads.geometry.iloc[int(np.argmin(roads.geometry.distance(row.geometry).to_numpy()))]
        if road.distance(row.geometry) > 1.0:
            continue
        at = road.project(row.geometry)
        a, b = road.interpolate(max(at - 1.0, 0.0)), road.interpolate(min(at + 1.0, road.length))
        along = np.array([b.x - a.x, b.y - a.y])
        along /= max(np.linalg.norm(along), 1e-9)
        across = np.array([-along[1], along[0]])
        centre = np.array([row.geometry.x, row.geometry.y])
        crossings.append((shapely.LineString([centre - across * 1.0, centre + across * 1.0]), f'osm:node/{row.osm_id}'))

    junctions = _junctions(roads)
    lanes = []
    for row in roads.itertuples():
        if getattr(row, 'oneway', None) == 'yes':
            continue
        if row.highway not in CENTRE_LINED and not (row.highway in CENTRE_LINED_WHEN_LANES_TAGGED and _lanes(row) >= 2):
            continue
        for part in shapely.get_parts(row.geometry.difference(junctions)):
            if part.geom_type == 'LineString' and part.length > DASH:
                lanes.append((part, float(row.geometry.project(shapely.Point(part.coords[0]))), f'osm:way/{row.osm_id}', DASH, GAP))
    return crossings, lanes


def _strips(box, surfaces) -> tuple[list, list]:
    """What OpenStreetMap says is painted along the kerbs of a carriageway: kerbside parking (`amenity=parking` areas
    on the street) as outlines, and cycle lanes (`cycleway*=lane`) as a strip of their own with a dashed line towards
    the traffic lane. A cycle lane passes outside parked cars."""
    mapped = osm.features(box)
    low = [shape for shape, row in surfaces if row['level'] == 'low']
    if mapped is None or not low:
        return [], []
    carriageway = shapely.unary_union(low)
    bands, edges = [], []
    parked = []
    for row in mapped[mapped.geom_type.isin(('Polygon', 'MultiPolygon'))].itertuples():
        tags = _tags(row)
        if tags.get('amenity') != 'parking' or tags.get('parking') not in ('street_side', 'lane') or tags.get('access') == 'private':
            continue
        bay = shapely.make_valid(row.geometry)
        if bay.intersection(carriageway).area < 0.8 * bay.area:
            continue
        parked.append(bay)
        outline = bay.boundary.intersection(carriageway.buffer(-PARKING_CLEAR))
        for part in shapely.get_parts(shapely.line_merge(outline) if outline.geom_type == 'MultiLineString' else outline):
            if part.geom_type == 'LineString' and part.length > 1.5:
                edges.append((part, 0.0, f'osm:{row.osm_type}/{row.osm_id}:bay', SOLID, 0.0))
    if parked:
        carriageway = carriageway.difference(shapely.unary_union(parked).buffer(0.05))
    lines = mapped[mapped.geom_type == 'LineString']
    roads = lines[lines.highway.isin(ROADS)]
    junctions = _junctions(roads)
    clear = junctions

    def sides_of(row) -> set:
        tags = _tags(row)
        return {side for side in ('left', 'right') for key in ('cycleway', 'cycleway:both', f'cycleway:{side}')
                if tags.get(key) == 'lane'}

    # The mouths of streets without lanes stay plain: a lane follows its own street's kerb, not the corner's.
    others = shapely.unary_union([row.geometry.buffer(SIDE_STREET, cap_style='flat') for row in roads.itertuples() if not sides_of(row)]
                                 or [shapely.Polygon()])
    junctions = shapely.unary_union([junctions, others])
    for row in roads.itertuples():
        tags = _tags(row)
        sides = sides_of(row)
        if not sides:
            continue
        try:
            width = max(float(tags.get('cycleway:both:width', tags.get('cycleway:width', CYCLE_LANE))), CYCLE_LANE)
        except ValueError:
            width = CYCLE_LANE
        for side in sides:
            reach = CORRIDOR if side == 'left' else -CORRIDOR
            corridor = row.geometry.buffer(reach, single_sided=True, cap_style='flat').difference(junctions)
            inner = carriageway.buffer(-width, join_style='mitre')
            band = carriageway.difference(inner).intersection(corridor)
            key = f'osm:way/{row.osm_id}:cycle-{side}'
            for n, part in enumerate(_polygons(band)):
                if part.area > 4.0:
                    bands.append((part, {'key': f'{key}#{n}' if n else key, 'source': 'osm', 'level': 'low', 'use': 'cycle-lane',
                                         'surface': '', 'rank': -1}))
            # The line lies its own half width inside the lane, so that it is painted on the lane's edge.
            edge = carriageway.buffer(-(width + 0.06), join_style='mitre').boundary.intersection(corridor)
            for part in shapely.get_parts(shapely.line_merge(edge) if edge.geom_type == 'MultiLineString' else edge):
                if part.geom_type == 'LineString' and part.length > 6.0:
                    edges.append((part, 0.0, key, CYCLE_DASH, CYCLE_GAP))

    # Kerbside parking lanes (cars parked on the carriageway along the kerb): a strip of its own surface, which
    # traffic does not polish; what it is paved with is the stored description's to say.
    for row in roads.itertuples():
        tags = _tags(row)
        for side in ('left', 'right'):
            if 'lane' not in (tags.get(f'parking:{side}'), tags.get('parking:both')):
                continue
            reach = CORRIDOR if side == 'left' else -CORRIDOR
            corridor = row.geometry.buffer(reach, single_sided=True, cap_style='flat').difference(clear)
            band = carriageway.difference(carriageway.buffer(-PARKING_LANE, join_style='mitre')).intersection(corridor)
            key = f'osm:way/{row.osm_id}:parking-{side}'
            for n, part in enumerate(_polygons(band)):
                if part.area > 6.0:
                    bands.append((part, {'key': f'{key}#{n}' if n else key, 'source': 'osm', 'level': 'low', 'use': 'parking-lane',
                                         'surface': '', 'rank': -2}))
    return bands, edges


class Ground:
    name = 'ground'
    code = (Path(lidar.__file__).parent,)

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(CELL + MARGIN)
        with sources.timed('street:surfaces'):
            surfaces = _surfaces(box)
            crossings, lanes = _paint(box)
            bands, edges = _strips(box, surfaces)
            surfaces += bands
            lanes += edges
        # Land nobody mapped: what the orthophoto shows, outside buildings (a roof's colour says nothing).
        mapped = osm.features(box)
        roofs = shapely.unary_union(list(mapped[mapped.building.notna() & mapped.geom_type.isin(('Polygon', 'MultiPolygon'))].geometry)) \
            if mapped is not None else None
        for n, (shape, name) in enumerate(aerial.outlines(sources, box, roofs)):
            centre = shape.representative_point()
            surfaces.append((shape, {'key': f'ortho:{name}:{centre.x:.1f}_{centre.y:.1f}', 'source': 'ortho', 'level': 'high',
                                     'use': name, 'surface': '', 'rank': UNMAPPED_RANK + n}))
        with sources.timed('street:lidar'):
            window = lidar.Window(*box)
        tree = shapely.STRtree([shape for shape, _ in surfaces])
        (i0, j1), (i1, j0) = cell_at(box[0], box[1]), cell_at(box[2], box[3])
        side = int(CELL / STEP) + 1 + 2 * HALO
        offsets = (np.arange(side) - HALO) * STEP
        stubs = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cell = cell_box_en(i, j)
                if not area.touches(cell):
                    continue
                east, north = np.meshgrid(cell[0] + offsets, cell[3] - offsets)
                heights = window.sample('ground', east.ravel(), north.ravel())
                wide = shapely.box(cell[0] - MARGIN, cell[1] - MARGIN, cell[2] + MARGIN, cell[3] + MARGIN)
                rows = []
                for k in sorted(tree.query(wide, predicate='intersects')):
                    shape, row = surfaces[k]
                    for n, part in enumerate(_polygons(shape.intersection(wide))):
                        rows.append({**row, 'key': row['key'] if n == 0 else f"{row['key']}#{n}", 'rings': _rings(part)})
                facts = {
                    'cell': [i, j],
                    'relief': {'step': STEP, 'halo': HALO, 'side': side, 'heights_mm': np.rint(heights * 1000).astype(int).tolist()},
                    'surfaces': rows,
                    'crossings': [{'key': key, 'line': np.asarray(line.coords).tolist(), 'width': CROSSING_WIDTH}
                                  for line, key in crossings if line.intersects(wide)],
                    'lanes': [{'key': key, 'line': np.asarray(part.coords).tolist(),
                               'start': round(start + line.project(shapely.Point(part.coords[0])), 3), 'dash': dash, 'gap': gap}
                              for line, start, key, dash, gap in lanes
                              for part in shapely.get_parts(line.intersection(wide)) if part.geom_type == 'LineString' and part.length > 0.5],
                }
                refs = [{'source': 'turku-lidar:pistepilvi', 'key': cell_key(i, j)},
                        {'source': 'turku-wfs:GIS:Katualueet', 'key': cell_key(i, j)},
                        {'source': 'turku-wfs:GIS:Viheralueet', 'key': cell_key(i, j)},
                        {'source': 'osm', 'key': cell_key(i, j)},
                        {'source': 'turku-ortho', 'key': cell_key(i, j)}]
                stubs.append(Stub(f'ground/{cell_key(i, j)}', cell, refs, facts))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """Source facts with a cover chosen for every surface; a stored description overrides covers by surface key
        (`covers`), the cover of unmapped land (`base`) and the kerb stone (`kerb`)."""
        stored = stored or {}
        chosen = stored.get('covers', {})
        surfaces = []
        for row in stub.facts['surfaces']:
            key = row['key'].split('#')[0]
            if key in chosen:
                cover = chosen[key]
            elif row['source'] in ('osm', 'ortho'):
                cover = covers.NAMED[row['use']]
            else:
                cover = covers.of_register(row['surface'], row['level'], row['source'] == 'green')
            surfaces.append({**row, 'cover': cover})
        return {**envelope(self.name, VERSION, stub, stored, 'w2-street'), **stub.facts, 'surfaces': surfaces,
                'base': stored.get('base', covers.NAMED['unmapped']), 'kerb': stored.get('kerb', covers.NAMED['kerb'])}

    def compile(self, description: dict) -> Output:
        return compile_cell(description)
