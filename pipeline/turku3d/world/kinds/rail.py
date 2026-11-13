"""Railway track: the two rails and the sleepers of every mapped track, one entity per 62.5 m cell that has any.

A track is an OpenStreetMap `railway=rail` way (also light rail and narrow gauge) on the ground: not in a tunnel
and not on a bridge. Its height is the lidar ground along the way, smoothed over a few metres (a track's gradient
changes over hundreds of metres; the scan's noise does not belong in the rail). The ballast bed under it is the
ground kind's (a surface `use: railway` along the same ways, `kinds/ground/__init__.py _surfaces`), so the skin
stays single-valued; where a street of the register crosses the track the street's surface stays and the rails lie
flush in it (a level crossing) without sleepers showing.

For other kinds (catenary masts and wires are the objects track's): `tracks(box)` gives the same ways with their
keys and tags, `level(way, stations)` the smoothed ground under a track, and the rail head is `RAIL_TOP` above it.

Schema: docs/specs/rail-description.md.
"""

from __future__ import annotations

import json
import math

import numpy as np
import shapely

from ..api import Area, Instances, Material, Output, Part, Stub, envelope
from ..frame import CELL, cell_at, cell_box_en, cell_key, to_engine
from ..street import lidar, osm, registers

VERSION = 1
RAILWAYS = ('rail', 'light_rail', 'narrow_gauge')
GAUGE = 1.524         # m between the rails' running edges: the Finnish gauge, when the way does not say
STEP = 1.0            # m between a rail's sections
SMOOTH = 6            # sections either side that a height is the median of
SLEEPER_PITCH = 0.6   # m between sleepers
SLEEPER = (2.6, 0.2, 0.26)   # m: length across the track, depth, width along it
SLEEPER_TOP = 0.03    # m of sleeper above the ballast
HEAD = 0.072          # m: width of the rail head
RAIL_HEIGHT = 0.172   # m: a 60 kg/m rail
RAIL_TOP = SLEEPER_TOP + RAIL_HEIGHT   # m from the smoothed ground to the top of the rail
FLUSH_TOP = 0.015     # m a rail stands out of a street it crosses
BED_HALF = 2.3        # m from a track's centre line to the edge of its ballast
STEEL = {'family': 'metal', 'params': {'colour': '#4a3a31', 'finish': 'weathered'}, 'color': (0.07, 0.042, 0.03), 'roughness': 0.8, 'metalness': 0.6}
RUNNING = {'family': 'metal', 'params': {'colour': '#9c9c9a', 'finish': 'gloss'}, 'color': (0.33, 0.33, 0.32), 'roughness': 0.35, 'metalness': 0.9}
CONCRETE = {'family': 'concrete', 'params': {'colour': '#8d8a84', 'finish': 'cast'}, 'color': (0.27, 0.25, 0.23), 'roughness': 0.9}


def _tags(row) -> dict:
    try:
        return json.loads(row.tags) if isinstance(row.tags, str) else {}
    except ValueError:
        return {}


def tracks(box) -> list[dict]:
    """The tracks on the ground that touch the survey box: `key`, the whole way as `line`, `gauge` (m) and whether
    it is `electrified` with a contact line."""
    mapped = osm.features(box)
    if mapped is None:
        return []
    found = []
    for row in mapped[(mapped.geom_type == 'LineString') & mapped.railway.isin(RAILWAYS)].itertuples():
        tags = _tags(row)
        if tags.get('tunnel', 'no') != 'no' or tags.get('bridge', 'no') != 'no' or tags.get('layer', '0') not in ('0', ''):
            continue
        try:
            gauge = float(tags.get('gauge', '')) / 1000.0
        except ValueError:
            gauge = GAUGE
        found.append({'key': f'osm:way/{row.osm_id}', 'line': row.geometry, 'gauge': gauge if 0.5 < gauge < 2.0 else GAUGE,
                      'electrified': tags.get('electrified') == 'contact_line'})
    return found


def level(line, stations, window=None) -> np.ndarray:
    """The ground under a track at the stations (m along the way): the median of the lidar ground over SMOOTH steps
    either side, so it depends on the place alone and neighbouring cells agree."""
    stations = np.asarray(stations, dtype=float)
    offsets = np.arange(-SMOOTH, SMOOTH + 1) * STEP
    at = np.clip(stations[:, None] + offsets[None, :], 0.0, line.length)
    points = shapely.get_coordinates(shapely.line_interpolate_point(line, at.ravel()))
    heights = (window.sample('ground', points[:, 0], points[:, 1]) if window is not None
               else lidar.ground_height(points[:, 0], points[:, 1])).reshape(at.shape)
    return np.nanmedian(heights, axis=1)


def beds(box) -> list[tuple]:
    """(polygon, key) of the ballast bed of every track on the ground that touches the box."""
    return [(track['line'].buffer(BED_HALF, cap_style='flat'), f"{track['key']}:bed") for track in tracks(box)]


class Rail:
    name = 'rail'

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(CELL)
        ways = tracks(box)
        if not ways:
            return []
        streets = registers.layer('GIS:Katualueet', box)
        paved = shapely.unary_union([shapely.make_valid(shapely.force_2d(g)) for g in streets.geometry if g.geom_type == 'Polygon']
                                    or [shapely.Polygon()])
        shapely.prepare(paved)
        window = lidar.Window(*area.bbox(CELL + (SMOOTH + 1) * STEP))
        (i0, j1), (i1, j0) = cell_at(box[0], box[1]), cell_at(box[2], box[3])
        stubs = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cell = cell_box_en(i, j)
                if not area.touches(cell):
                    continue
                square = shapely.box(*cell)
                rows = []
                for way in ways:
                    line = way['line']
                    inside = line.intersection(square)
                    for n, part in enumerate(p for p in shapely.get_parts(inside) if p.geom_type == 'LineString' and p.length > 0.5):
                        s0, s1 = sorted((line.project(shapely.Point(part.coords[0])), line.project(shapely.Point(part.coords[-1]))))
                        if s1 - s0 < 0.5:
                            continue
                        # Sections on whole steps of the way, so a rail's joints fall on the same places in both cells.
                        inner = np.arange(math.floor(s0 / STEP) + 1, math.ceil(s1 / STEP)) * STEP
                        stations = np.concatenate([[s0], inner, [s1]])
                        points = shapely.get_coordinates(shapely.line_interpolate_point(line, stations))
                        heights = level(line, stations, window)
                        if not np.isfinite(heights).all():
                            continue
                        flush = shapely.contains_xy(paved, points[:, 0], points[:, 1])
                        rows.append({'key': way['key'] if n == 0 else f"{way['key']}#{n}", 'gauge': way['gauge'],
                                     'electrified': way['electrified'], 'start': round(float(s0), 3),
                                     'stations': np.round(stations, 3).tolist(),
                                     'points': np.round(np.column_stack([points, heights]), 3).tolist(),
                                     'flush': [bool(v) for v in flush]})
                if rows:
                    refs = [{'source': 'osm', 'key': cell_key(i, j)}, {'source': 'turku-lidar:pistepilvi', 'key': cell_key(i, j)},
                            {'source': 'turku-wfs:GIS:Katualueet', 'key': cell_key(i, j)}]
                    stubs.append(Stub(f'rail/{cell_key(i, j)}', cell, refs, {'cell': [i, j], 'tracks': rows}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """Source facts; a stored description may set the materials (`steel`, `running`, `sleeper`) and the
        `sleeper_pitch`."""
        stored = stored or {}
        return {**envelope(self.name, VERSION, stub, stored, 'w2-street'), **stub.facts,
                'steel': stored.get('steel', STEEL), 'running': stored.get('running', RUNNING),
                'sleeper': stored.get('sleeper', CONCRETE), 'sleeper_pitch': stored.get('sleeper_pitch', SLEEPER_PITCH)}

    def compile(self, description: dict) -> Output:
        i, j = description['cell']
        origin = np.array([i * CELL, 0.0, j * CELL])
        pitch = float(description['sleeper_pitch'])
        sides, tops, placed = [], [], []
        for track in description['tracks']:
            points = np.asarray(track['points'], float)
            stations = np.asarray(track['stations'], float)
            flush = np.asarray(track['flush'], bool)
            centre = np.column_stack(to_engine(points[:, 0], points[:, 1], points[:, 2])) - origin
            run = np.gradient(centre[:, [0, 2]], axis=0)
            run /= np.maximum(np.linalg.norm(run, axis=1, keepdims=True), 1e-9)
            across = np.column_stack([-run[:, 1], np.zeros(len(run)), run[:, 0]])
            top = np.where(flush, FLUSH_TOP, RAIL_TOP)
            foot = np.where(flush, FLUSH_TOP - 0.1, SLEEPER_TOP)
            for side in (-1.0, 1.0):
                middle = centre + across * side * (track['gauge'] + HEAD) / 2
                sides.append(_bar(middle, across, foot, top, stations))
                tops.append(_cap(middle, across, top, stations))
            # Sleepers on whole pitches of the way: the cell that holds a sleeper's centre draws it.
            first, last = math.ceil(stations[0] / pitch), math.ceil(stations[-1] / pitch)
            for k in range(first, last):
                s = k * pitch
                at = np.array([np.interp(s, stations, centre[:, c]) for c in range(3)])
                if np.interp(s, stations, flush.astype(float)) > 0.0:
                    continue
                n = min(int(np.searchsorted(stations, s)), len(stations) - 1)
                bearing = math.atan2(run[n, 0], -run[n, 1])   # engine x is east, engine z is south
                placed.append([at[0], at[1] + SLEEPER_TOP - SLEEPER[1], at[2], math.pi / 2 - (bearing + math.pi / 2), 1.0, 1.0, 1.0])
        parts = []
        for name, meshes, cover in (('rails', sides, description['steel']), ('running', tops, description['running'])):
            positions, triangles, uvs = _merge(meshes)
            parts.append(Part(name, positions, triangles,
                              Material(cover['family'], cover.get('params', {}), tuple(cover['color']), cover.get('roughness', 0.8),
                                       cover.get('metalness', 0.0)), uvs=uvs))
        base = float(np.floor(min(part.positions[:, 1].min() for part in parts) - 1.0))
        for part in parts:
            part.positions[:, 1] -= base
        instances = []
        if placed:
            rows = np.array(placed)
            rows[:, 1] -= base
            cover = description['sleeper']
            instances.append(Instances([_sleeper(Material(cover['family'], cover.get('params', {}), tuple(cover['color']), cover.get('roughness', 0.9)))],
                                       np.round(rows, 4)))
        return Output((float(origin[0]), base, float(origin[2])), parts, instances, error_m={0: 0.03})


def _merge(meshes):
    positions, triangles, uvs, count = [], [], [], 0
    for p, t, u in meshes:
        positions.append(p)
        triangles.append(t + count)
        uvs.append(u)
        count += len(p)
    return np.concatenate(positions), np.concatenate(triangles), np.concatenate(uvs)


def _quads(a, b, c, d, u, v, outward):
    """Quads a-b (one row of points to the next) over d-c, turned to face `outward`; flat shading needs its own
    vertices, so every quad row is a block of its own."""
    n = len(a)
    block = np.concatenate([a, b, c, d])
    k = np.arange(n)
    faces = np.concatenate([np.column_stack([k, k + n, k + 2 * n]), np.column_stack([k, k + 2 * n, k + 3 * n])])
    normal = np.cross(block[faces[:, 1]] - block[faces[:, 0]], block[faces[:, 2]] - block[faces[:, 0]])
    flip = np.einsum('ij,ij->i', normal, np.tile(outward, (2, 1))) < 0
    faces[flip] = faces[flip][:, ::-1]
    uv = np.concatenate([np.column_stack([u[0], np.full(n, v[0])]), np.column_stack([u[1], np.full(n, v[0])]),
                         np.column_stack([u[1], np.full(n, v[1])]), np.column_stack([u[0], np.full(n, v[1])])])
    return block, faces, uv


def _bar(middle, across, foot, top, stations):
    """The two sides, the underside and the ends of a rail: with `_cap` a closed bar HEAD wide from `foot` to `top`
    above the track's level."""
    up = np.array([0.0, 1.0, 0.0])
    left, right = middle - across * HEAD / 2, middle + across * HEAD / 2
    high, low = up[None, :] * top[:, None], up[None, :] * foot[:, None]
    u = (stations[:-1], stations[1:])
    blocks = [
        _quads((left + high)[:-1], (left + high)[1:], (left + low)[1:], (left + low)[:-1], u, (0.0, RAIL_HEIGHT), -across[:-1]),
        _quads((right + high)[:-1], (right + high)[1:], (right + low)[1:], (right + low)[:-1], u, (0.0, RAIL_HEIGHT), across[:-1]),
        _quads((left + low)[:-1], (left + low)[1:], (right + low)[1:], (right + low)[:-1], u, (0.0, HEAD), np.tile(-up, (len(middle) - 1, 1))),
    ]
    for end, sign in ((0, -1.0), (-1, 1.0)):
        run = (middle[1] - middle[0]) if end == 0 else (middle[-1] - middle[-2])
        run = run / max(np.linalg.norm(run), 1e-9) * sign
        one = lambda p: p[[end]]
        blocks.append(_quads(one(left + high), one(right + high), one(right + low), one(left + low),
                             (np.zeros(1), np.full(1, HEAD)), (0.0, RAIL_HEIGHT), run[None, :]))
    return _merge(blocks)


def _cap(middle, across, top, stations):
    """The running surface of a rail."""
    up = np.array([0.0, 1.0, 0.0])
    left, right = middle - across * HEAD / 2, middle + across * HEAD / 2
    high = up[None, :] * top[:, None]
    return _quads((left + high)[:-1], (left + high)[1:], (right + high)[1:], (right + high)[:-1],
                  (stations[:-1], stations[1:]), (0.0, HEAD), np.tile(up, (len(middle) - 1, 1)))


def _sleeper(material: Material) -> Part:
    """One sleeper: a closed box, its length along +X, its base at y = 0."""
    x, y, z = SLEEPER[0] / 2, SLEEPER[1], SLEEPER[2] / 2
    corners = np.array([[sx * x, sy * y, sz * z] for sx in (-1, 1) for sy in (0, 1) for sz in (-1, 1)])
    quads = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]
    positions, triangles = [], []
    for q, (a, b, c, d) in enumerate(quads):
        positions.append(corners[[a, b, c, d]])
        triangles += [[4 * q, 4 * q + 1, 4 * q + 2], [4 * q, 4 * q + 2, 4 * q + 3]]
    positions, triangles = np.concatenate(positions), np.array(triangles)
    centre = np.array([0.0, y / 2, 0.0])
    normal = np.cross(positions[triangles[:, 1]] - positions[triangles[:, 0]], positions[triangles[:, 2]] - positions[triangles[:, 0]])
    flip = np.einsum('ij,ij->i', normal, positions[triangles[:, 0]] - centre) < 0
    triangles[flip] = triangles[flip][:, ::-1]
    return Part('sleeper', positions, triangles, material, uvs=positions[:, [0, 2]] + positions[:, [1, 1]])
