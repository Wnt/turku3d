"""What a bridge is, from the map and the scan: its deck (the ground kind's `decks.spans`), where it stands on its
abutments, where its piers are, what kind of bridge it is and what passes under it.

Every answer depends on the bridge alone: a deck is found again from a box around its own mapped way, so every
location and every region unit that touches a bridge describes it the same way.

- Stations are metres along the deck's axis (`decks.spans` draws the mapped way on to both abutments).
- The water span is where the axis crosses mapped open water (OpenStreetMap `natural=water`).
- An abutment's face is where the ground beside the deck has come down from the street to the bank it stands on: the
  lowest land next to the water, measured on both sides of the deck outside its outline. Between the face and the
  deck's end the abutment fills the space under the deck; between the face and the water a bank path passes under.
- Piers are vertical clusters of returns between the water and the deck (the old generator's
  `coarse/bridge_survey.pier_stations`): airborne lidar sees the exposed upper part of a pier beside the deck, never
  the deck's underside. Missing returns do not prove that a span has no pier: the type's longest span completes the
  layout.
- Gates mapped under the deck at bank level (`barrier=gate`) mark a passage through an abutment.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import shapely

from ...street import lidar, osm
from ..ground import decks
from ..ground.mesh import WATER_LEVEL

FIND = 30.0          # m around a bridge way: the box its deck is found again from
SIDE_OUT = 1.5       # m outside the deck outline where the ground beside it is read
BANK_STRIP = 6.0     # m of land next to the water whose ground is the bank the abutment stands on
FACE_RISE = 0.6      # m above that bank where the abutment's face stands
PIER_CLASSES = (1, 10, 12, 17)   # unclassified, bridge, overlap and bridge-deck returns: what a pier's side gives
PIER_LOW = 0.4       # m above the water where a pier's returns begin to count (waves and boats lie lower)
PIER_UNDER = 1.0     # m under the deck's top where they stop (the deck's own edge)
PIER_EDGE = 2.0      # m from the water's edges where a cluster is the bank, not a pier
PIER_GROUP = 3.0     # m of empty axis that separate two clusters
PIER_LENGTH = 6.0    # m: a longer cluster along the axis is not a pier (a moored boat, a quay)
PIER_POINTS = 10
GATE_REACH = 2.0     # m outside the outline within which a mapped gate still belongs to a passage under the deck
PASSAGE_REACH = 4.0  # m from the deck's side within which a path the map ends there goes on through the abutment
PASSAGE_JOIN = 6.0   # m along the axis within which path ends on the two sides are one passage under the deck
PATHS = ('footway', 'cycleway', 'path', 'pedestrian', 'bridleway', 'steps')
RAILS = ('rail', 'tram', 'light_rail', 'narrow_gauge')


def _tags(row) -> dict:
    try:
        return json.loads(row.tags) if isinstance(row.tags, str) else {}
    except ValueError:
        return {}


def _year(tags: dict) -> int | None:
    text = str(tags.get('start_date', tags.get('construction_year', '')))[:4]
    return int(text) if text.isdigit() else None


@lru_cache(maxsize=64)
def _deck(key: str, box: tuple) -> dict | None:
    for deck in decks.spans(box):
        if deck['key'] == key:
            return deck
    return None


def candidates(box) -> list[tuple[str, tuple]]:
    """(deck key, the box it is found again from) of every deck touching the box."""
    found = []
    for deck in decks.spans(box):
        e0, n0, e1, n1 = deck['axis'].bounds
        own = (round(e0 - FIND), round(n0 - FIND), round(e1 + FIND), round(n1 + FIND))
        found.append((deck['key'], own))
    return found


def _sides(area, axis) -> tuple[list, list]:
    """The deck outline's two long side chains as point lists ordered along the axis, left of it first."""
    ring = np.asarray(area.exterior.coords)
    sides = {1: [], -1: []}
    for a, b in zip(ring[:-1], ring[1:]):
        middle = (a + b) / 2
        s = axis.project(shapely.Point(middle))
        p0 = np.asarray(axis.interpolate(max(s - 0.5, 0)).coords[0])
        p1 = np.asarray(axis.interpolate(min(s + 0.5, axis.length)).coords[0])
        tangent = (p1 - p0) / max(np.linalg.norm(p1 - p0), 1e-9)
        edge = b - a
        if np.linalg.norm(edge) < 1e-6 or abs(edge @ tangent) < 0.7 * np.linalg.norm(edge):
            continue
        normal = np.array([-tangent[1], tangent[0]])
        sides[1 if (middle - np.asarray(axis.interpolate(s).coords[0])) @ normal > 0 else -1].append((a, b))
    chains = []
    for sign in (1, -1):
        points = [p for segment in sides[sign] for p in segment]
        if not points:
            chains.append([])
            continue
        points = np.asarray(points)
        stations = shapely.line_locate_point(axis, shapely.points(points))
        order = np.argsort(stations, kind='stable')
        kept, last = [], -np.inf
        for k in order:
            if stations[k] - last > 0.05:
                kept.append(points[k].tolist())
                last = stations[k]
        chains.append(kept)
    return chains[0], chains[1]


def _water_spans(axis, water) -> list[list[float]]:
    spans = []
    for part in shapely.get_parts(axis.intersection(water)):
        if part.geom_type != 'LineString' or part.length < 1.0:
            continue
        a, b = (axis.project(shapely.Point(part.coords[k])) for k in (0, -1))
        spans.append([round(min(a, b), 2), round(max(a, b), 2)])
    return sorted(spans)


def _side_ground(axis, area, stations, half) -> np.ndarray:
    """The lower of the two grounds just outside the deck's sides at each station."""
    points = shapely.get_coordinates(shapely.line_interpolate_point(axis, stations))
    ahead = shapely.get_coordinates(shapely.line_interpolate_point(axis, np.minimum(stations + 0.5, axis.length)))
    behind = shapely.get_coordinates(shapely.line_interpolate_point(axis, np.maximum(stations - 0.5, 0.0)))
    run = ahead - behind
    run /= np.maximum(np.linalg.norm(run, axis=1, keepdims=True), 1e-9)
    normal = np.column_stack([-run[:, 1], run[:, 0]])
    found = []
    for sign in (1.0, -1.0):
        # Walk outwards until the point is outside the outline, then SIDE_OUT more.
        reach = np.full(len(points), half)
        for _ in range(12):
            probe = points + normal * sign * reach[:, None]
            inside = shapely.contains_xy(area, probe[:, 0], probe[:, 1])
            if not inside.any():
                break
            reach = np.where(inside, reach + 1.0, reach)
        probe = points + normal * sign * (reach + SIDE_OUT)[:, None]
        low, high = probe.min(axis=0), probe.max(axis=0)
        window = lidar.Window(low[0] - 2, low[1] - 2, high[0] + 2, high[1] + 2)
        found.append(window.sample('ground', probe[:, 0], probe[:, 1]))
    return np.fmin(found[0], found[1])


def _faces(stations, ground, water, length) -> tuple[float, float, float, float]:
    """(face at the start, face at the end, bank level there, bank level at the end) in stations along the axis."""
    if not water:
        return 0.0, length, float(np.nanmin(ground[: len(ground) // 2 + 1])), float(np.nanmin(ground[len(ground) // 2:]))
    first, last = water[0][0], water[-1][1]
    out = []
    for start, edge, sign in ((0.0, first, 1), (length, last, -1)):
        strip = (stations >= edge - BANK_STRIP) & (stations <= edge) if sign > 0 else (stations <= edge + BANK_STRIP) & (stations >= edge)
        bank = float(np.nanmedian(ground[strip])) if strip.any() and np.isfinite(ground[strip]).any() else WATER_LEVEL + 1.0
        if sign > 0:
            between = np.flatnonzero((stations >= start) & (stations <= edge) & (ground <= bank + FACE_RISE))
            face = float(stations[between[0]]) if len(between) else edge
        else:
            between = np.flatnonzero((stations <= start) & (stations >= edge) & (ground <= bank + FACE_RISE))
            face = float(stations[between[-1]]) if len(between) else edge
        out.append((face, bank))
    return out[0][0], out[1][0], out[0][1], out[1][1]


def _returns(axis, half: float):
    """Lidar returns (e, n, h, class) around the deck."""
    box = axis.buffer(half + 4.0).bounds
    found = []
    for name in lidar.shared.names(box):
        path = lidar.shared.laz_path(name)
        if path is None:
            continue
        las = lidar.shared.read(path)
        x, y = np.asarray(las.x), np.asarray(las.y)
        keep = (x > box[0]) & (x < box[2]) & (y > box[1]) & (y < box[3])
        found.append(np.column_stack([x[keep], y[keep], np.asarray(las.z)[keep], np.asarray(las.classification)[keep]]))
    return np.concatenate(found) if found else np.zeros((0, 4))


def _piers(axis, half, top_at, water, points) -> list[float]:
    """Stations of vertical return clusters between the water and the deck, over the water."""
    if not len(points) or not water:
        return []
    along = shapely.line_locate_point(axis, shapely.points(points[:, :2]))
    across = shapely.distance(axis, shapely.points(points[:, :2]))
    wet = np.zeros(len(points), bool)
    for a, b in water:
        wet |= (along > a + PIER_EDGE) & (along < b - PIER_EDGE)
    top = top_at(along)
    chosen = (wet & (across < half + 0.5) & np.isin(points[:, 3], PIER_CLASSES)
              & (points[:, 2] > WATER_LEVEL + PIER_LOW) & (points[:, 2] < top - PIER_UNDER))
    index = np.flatnonzero(chosen)
    if not len(index):
        return []
    bins, counts = np.unique(np.floor(along[index]).astype(int), return_counts=True)
    occupied = bins[counts >= 3]
    if not len(occupied):
        return []
    found = []
    for group in np.split(occupied, np.flatnonzero(np.diff(occupied) > PIER_GROUP) + 1):
        if group[-1] - group[0] > PIER_LENGTH:
            continue
        cluster = index[(along[index] >= group[0]) & (along[index] < group[-1] + 1)]
        if len(cluster) < PIER_POINTS or (np.ptp(points[cluster, 2]) < 0.25 and np.ptp(across[cluster]) < half * 0.5):
            continue
        found.append(round(float(np.median(along[cluster])), 2))
    return found


def _gates(mapped, axis, area) -> list[dict]:
    """Mapped gates under the deck's ends: a passage through the abutment, as (station, lateral offset, tags)."""
    nodes = mapped[(mapped.geom_type == 'Point') & (mapped.barrier == 'gate')] if 'barrier' in mapped else mapped.iloc[:0]
    found = []
    for row in nodes.itertuples():
        if area.distance(row.geometry) > GATE_REACH:
            continue
        s = axis.project(row.geometry)
        found.append({'station': round(float(s), 2), 'key': f'osm:node/{row.osm_id}', 'locked': _tags(row).get('locked') == 'yes'})
    return found


def _passages(mapped, axis, area, length: float) -> list[dict]:
    """Bank paths at the deck's sides: a path the map stops at a side (it goes on through the abutment, unmapped) or
    draws across the deck's outline under it. One row per station, with the sides it opens and the paths' keys."""
    lines = mapped[mapped.geom_type == 'LineString']
    ring = area.exterior
    found = []
    for row in lines.itertuples():
        tags = _tags(row)
        if tags.get('highway') not in PATHS or tags.get('highway') == 'steps' or tags.get('bridge', 'no') != 'no':
            continue
        if str(tags.get('layer', '0')).lstrip('-').isdigit() and int(tags.get('layer', '0')) > 0:
            continue
        line = row.geometry
        ends = [shapely.Point(line.coords[0]), shapely.Point(line.coords[-1])]
        points = [p for p in ends if ring.distance(p) <= PASSAGE_REACH and not area.buffer(-1.0).contains(p)]
        if line.crosses(area.buffer(-1.0)):
            points += [shapely.Point(c) for c in shapely.get_coordinates(line.intersection(ring))]
        for point in points:
            station = float(axis.project(point))
            if not 0.5 < station < length - 0.5:
                continue
            centre = np.asarray(axis.interpolate(station).coords[0])
            ahead = np.asarray(axis.interpolate(min(station + 0.5, length)).coords[0]) - np.asarray(axis.interpolate(max(station - 0.5, 0)).coords[0])
            left = np.array([-ahead[1], ahead[0]])
            side = 0 if (np.asarray(point.coords[0]) - centre) @ left > 0 else 1
            width = str(tags.get('width', '')).replace(',', '.')
            found.append({'station': station, 'sides': {side}, 'keys': {f'osm:way/{row.osm_id}'},
                          'width': float(width) if width.replace('.', '', 1).isdigit() else None})
    found.sort(key=lambda p: p['station'])
    merged = []
    for passage in found:
        if merged and passage['station'] - merged[-1]['station'] <= PASSAGE_JOIN:
            last = merged[-1]
            last['station'] = (last['station'] + passage['station']) / 2
            last['sides'] |= passage['sides']
            last['keys'] |= passage['keys']
            last['width'] = last['width'] or passage['width']
            continue
        merged.append(passage)
    return [{'station': round(p['station'], 2), 'sides': sorted(p['sides']), 'keys': sorted(p['keys']), 'width': p['width']}
            for p in merged]


def facts(key: str, box: tuple) -> dict | None:
    """The facts of the bridge whose deck is `key`, found again from `box` (`candidates`)."""
    deck = _deck(key, box)
    if deck is None:
        return None
    mapped = osm.features(box)
    axis, area = deck['axis'], deck['area']
    length = axis.length
    half = area.area / max(length, 1e-9) / 2
    # The bridge's own tags: the outline (man_made=bridge) around the way wins over the way's.
    way_id = int(key.rsplit('/', 1)[1])
    way = mapped[(mapped.osm_id == way_id) & (mapped.geom_type == 'LineString')]
    way_tags = _tags(way.iloc[0]) if len(way) else {}
    outline_tags = {}
    middle = axis.interpolate(0.5, normalized=True)
    for row in mapped[mapped.tags.str.contains('"man_made": "bridge"', regex=False, na=False)].itertuples():
        shape = row.geometry
        if shape.geom_type == 'LineString' and shape.is_closed and len(shape.coords) >= 4:
            shape = shapely.Polygon(shape.coords)
        if shape.geom_type in ('Polygon', 'MultiPolygon') and shape.buffer(1.0).contains(middle):
            outline_tags = _tags(row)
            break
    tags = {**way_tags, **{k: v for k, v in outline_tags.items() if k != 'man_made'}}
    # What uses the deck: the members of the deck, from the ways on it.
    lines = mapped[(mapped.geom_type == 'LineString')]
    on = [(_tags(row)) for row in lines.itertuples() if _tags(row).get('bridge', 'no') != 'no' and row.geometry.intersects(area.buffer(-0.5))]
    roads = [t for t in on if t.get('highway') and t.get('highway') not in PATHS]
    rails = [t for t in on if t.get('railway') in RAILS]
    use = 'rail' if rails and not roads else ('road' if roads else 'foot')
    water_rows = mapped[mapped.geom_type.isin(('Polygon', 'MultiPolygon')) & (mapped.natural == 'water')]
    wet = shapely.unary_union([shapely.make_valid(g) for g in water_rows.geometry]) if len(water_rows) else shapely.Polygon()
    water = _water_spans(axis, wet)
    stations = np.arange(0.0, length + 0.01, 1.0)
    ground = _side_ground(axis, area, stations, half)
    face0, face1, bank0, bank1 = _faces(stations, ground, water, length)

    def top_at(s):
        return deck['ends'][0] + (deck['ends'][1] - deck['ends'][0]) * np.asarray(s) / max(length, 1e-9)

    points = _returns(axis, half)
    measured = _piers(axis, half, top_at, water, points)
    left, right = _sides(area, axis)
    return {
        'key': key,
        'name': tags.get('name', ''),
        'wikidata': tags.get('wikidata', ''),
        'year': _year(tags),
        'structure': tags.get('bridge:structure', ''),
        'material': tags.get('bridge:material', tags.get('material', '')),
        'use': use,
        'rings': [np.round(np.asarray(area.exterior.coords)[:-1], 3).tolist()],
        'axis': np.round(np.asarray(axis.coords), 3).tolist(),
        'ends': [round(v, 3) for v in deck['ends']],
        'sides': [np.round(left, 3).tolist(), np.round(right, 3).tolist()],
        'water': water,
        'faces': [round(face0, 2), round(face1, 2)],
        'banks': [round(bank0, 2), round(bank1, 2)],
        'ground': {'step': 1.0, 'heights': np.round(np.nan_to_num(ground, nan=WATER_LEVEL), 2).tolist()},
        'piers': {'measured': measured},
        'gates': _gates(mapped, axis, area),
        'passages': _passages(mapped, axis, area, length),
    }
