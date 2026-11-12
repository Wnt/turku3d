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
from . import covers
from .mesh import compile_cell

VERSION = 1
STEP = 0.5            # relief node spacing: the lidar raster's own
HALO = 8              # relief nodes around the cell: what a kerb's two levels are carried from
MARGIN = HALO * STEP  # surfaces reach this far beyond the cell for the same reason
ROADS = ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'living_street',
         'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link')
CENTRE_LINED = ('primary', 'secondary', 'tertiary', 'unclassified')
CROSSING_WIDTH = 4.0
JUNCTION_CLEAR = 10.0   # lane lines stop this far from where roads meet
DASH, GAP = 3.0, 9.0    # centre line of a street with a limit of 50 km/h or less


def _tags(row) -> dict:
    try:
        return json.loads(row.tags) if isinstance(row.tags, str) else {}
    except ValueError:
        return {}


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
        use = (getattr(row, 'Katuosalaji', None) or '')
        level = 'low' if use.lower() in covers.LOW_USES else 'high'
        found.append((shapely.make_valid(shapely.force_2d(row.geometry)), {
            'key': f'street:{row.feature_key}', 'source': 'street', 'level': level, 'use': use,
            'surface': getattr(row, 'Pintamateriaali', None) or '', 'rank': 0 if level == 'low' else 1}))
    greens = registers.layer('GIS:Viheralueet', box)
    for row in greens.itertuples():
        if row.geometry.geom_type != 'Polygon':
            continue
        found.append((shapely.make_valid(shapely.force_2d(row.geometry)), {
            'key': f'green:{row.feature_key}', 'source': 'green', 'level': 'high',
            'use': getattr(row, 'Viheralueosalaji', None) or '', 'surface': getattr(row, 'Pintamateriaali', None) or '',
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
            found.append((shape, {'key': key, 'source': 'osm', 'level': 'high', 'use': name, 'surface': '', 'rank': 3 + order}))
    return found


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

    # Where roads meet, lane lines stop.
    ends = {}
    for row in roads.itertuples():
        for point in row.geometry.coords:
            ends.setdefault((round(point[0], 2), round(point[1], 2)), set()).add(row.osm_id)
    junctions = shapely.unary_union([shapely.Point(p).buffer(JUNCTION_CLEAR) for p, ways in ends.items() if len(ways) > 1]
                                    or [shapely.Polygon()])
    lanes = []
    for row in roads[roads.highway.isin(CENTRE_LINED)].itertuples():
        if getattr(row, 'oneway', None) == 'yes':
            continue
        for part in shapely.get_parts(row.geometry.difference(junctions)):
            if part.geom_type == 'LineString' and part.length > DASH:
                lanes.append((part, float(row.geometry.project(shapely.Point(part.coords[0]))), f'osm:way/{row.osm_id}'))
    return crossings, lanes


class Ground:
    name = 'ground'
    code = (Path(lidar.__file__).parent,)

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(CELL + MARGIN)
        with sources.timed('street:surfaces'):
            surfaces = _surfaces(box)
            crossings, lanes = _paint(box)
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
                               'start': round(start + line.project(shapely.Point(part.coords[0])), 3), 'dash': DASH, 'gap': GAP}
                              for line, start, key in lanes
                              for part in shapely.get_parts(line.intersection(wide)) if part.geom_type == 'LineString' and part.length > 0.5],
                }
                refs = [{'source': 'turku-lidar:pistepilvi', 'key': cell_key(i, j)},
                        {'source': 'turku-wfs:GIS:Katualueet', 'key': cell_key(i, j)},
                        {'source': 'turku-wfs:GIS:Viheralueet', 'key': cell_key(i, j)},
                        {'source': 'osm', 'key': cell_key(i, j)}]
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
            elif row['source'] == 'osm':
                cover = covers.NAMED[row['use']]
            else:
                cover = covers.of_register(row['surface'], row['level'], row['source'] == 'green')
            surfaces.append({**row, 'cover': cover})
        return {**envelope(self.name, VERSION, stub, stored, 'w2-street'), **stub.facts, 'surfaces': surfaces,
                'base': stored.get('base', covers.NAMED['unmapped']), 'kerb': stored.get('kerb', covers.NAMED['kerb'])}

    def compile(self, description: dict) -> Output:
        return compile_cell(description)
