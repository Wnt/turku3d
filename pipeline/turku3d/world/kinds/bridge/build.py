"""A bridge's structure from its description: abutments, piers, the spans' girders or arches, edge beams, parapets,
end pylons and the openings in the abutments. The deck's walking surface and slab are the ground kind's
(`ground/decks.py`, `ground/mesh.py`); everything here hangs under it or stands on its edges.

Engine frame of the entity: origin at the axis's first point, x east, z south, y up from `base`.
"""

from __future__ import annotations

import math

import numpy as np
import shapely

from ...api import Instances, Material, Output
from ...frame import ORIGIN_E, ORIGIN_N
from ...objects.common import yaw_of_run
from ...objects.mesh import Mesh, rgb_hex
from ..ground import decks
from ..ground.mesh import BED_DEPTH, WATER_LEVEL
from .solids import Solids

STEP = 1.0              # m between the sections a band is drawn through
TUCK = 0.05             # m a structure reaches up into the deck's slab, so no seam shows under it
EDGE = 0.45             # m: width of an edge beam under a parapet
EDGE_UP = 0.15          # m an edge beam stands above the carriageway
EDGE_DOWN = 0.2         # m it hangs below the slab
ROOT = 1.0              # m an abutment reaches under the lowest ground beside it
CLEAR_WALK = 2.4        # m a girder keeps above the bank where a path passes under the deck
PARAPET_DROP = 0.6      # m of fall beside the deck from which a parapet stands
PANEL = 2.0             # m: length of a railing panel
BALUSTER_PITCH = 0.3    # m between balusters
DIE_PITCH = 3.0         # m between the solid blocks of a balustrade


def _hex_material(family: str, params: dict, colour: str, roughness: float = 0.85, metalness: float = 0.0) -> Material:
    return Material(family, {**params, 'colour': colour}, rgb_hex(colour), roughness, metalness)


def materials(style: dict) -> dict[str, Material]:
    body = style.get('body_colour')
    return {
        'stone': _hex_material('granite', {'colour2': '#7c766d', 'finish': 'ashlar', 'relief': 'ashlar', 'scale_m': 1.2, 'course_m': 0.6}, '#a39c90'),
        'concrete': _hex_material('concrete', {'finish': 'cast', 'scale_m': 1.2}, '#9b988f'),
        'steel': _hex_material('metal', {'finish': 'matt'}, body or '#6f767a', 0.6, 0.5),
        'wood': _hex_material('weatherboard', {'orientation': 'horizontal', 'profile': 'shiplap', 'scale_m': 0.15}, '#6d5a45'),
        'cast': _hex_material('concrete', {'finish': 'cast', 'scale_m': 0.8}, '#cec7b9', 0.8),
        'pylon': _hex_material('granite', {'colour2': '#9a9284', 'finish': 'slab', 'relief': 'ashlar', 'scale_m': 0.9, 'course_m': 0.9}, '#bdb4a5', 0.8),
        'bronze': _hex_material('copper', {'finish': 'weathered'}, '#4b6658', 0.5, 0.6),
        'rail': _hex_material('metal', {'finish': 'matt'}, style.get('rail_colour', '#2e3133'), 0.5, 0.6),
        'void': _hex_material('concrete', {'finish': 'cast', 'scale_m': 1.0}, '#1c1b1a', 0.95),
    }


def style(description: dict) -> dict:
    """Type rules from the map's facts (use, era, OpenStreetMap structure and material), then the stored profile."""
    year, use = description.get('year'), description.get('use', 'road')
    material = description.get('material', '')
    old = year is not None and year < 1940
    body = {'stone': 'stone', 'masonry': 'stone', 'granite': 'stone', 'steel': 'steel', 'metal': 'steel', 'wood': 'wood',
            'concrete': 'concrete', 'reinforced_concrete': 'concrete'}.get(material, 'steel' if use == 'foot' else 'concrete')
    structure = description.get('structure') or 'beam'
    out = {
        'body': body,
        'support': 'stone' if old or body == 'stone' else 'concrete',
        'structure': structure,
        'parapets': {'approach': 'solid' if old and use != 'foot' else 'railing', 'span': 'solid' if body == 'stone' else 'railing'},
        'rail_height': 1.0 if use == 'foot' else 1.1,
        'max_span': {'arch': 60.0, 'cable-stayed': 80.0, 'suspension': 120.0}.get(structure, 35.0 if use == 'foot' else 40.0),
        'slab': decks.SLAB,
    }
    profile = description.get('profile') or {}
    for key, value in profile.items():
        if key in ('name', 'evidence'):
            continue
        out[key] = {**out[key], **value} if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


class Frame:
    """The deck in plan: stations along the axis, the edges' lateral offsets, the carriageway level."""

    def __init__(self, description: dict):
        self.axis = shapely.LineString(description['axis'])
        self.length = self.axis.length
        self.ends = description['ends']
        first = np.asarray(description['axis'][0], float)
        self.e0, self.n0 = float(first[0]), float(first[1])
        lefts, rights = (np.asarray(chain, float).reshape(-1, 2) for chain in description['sides'])
        self.lat = []
        for chain, sign in ((lefts, 1.0), (rights, -1.0)):
            if len(chain) < 2:
                half = float(shapely.Polygon(description['rings'][0]).area / max(self.length, 1e-9) / 2)
                self.lat.append((np.array([0.0, self.length]), np.array([sign * half, sign * half])))
                continue
            s = shapely.line_locate_point(self.axis, shapely.points(chain))
            offset = np.array([self._offset(p, si) for p, si in zip(chain, s)])
            order = np.argsort(s)
            self.lat.append((s[order], offset[order]))

    def _normal(self, s):
        s = np.atleast_1d(np.asarray(s, float))
        a = shapely.get_coordinates(shapely.line_interpolate_point(self.axis, np.maximum(s - 0.5, 0.0)))
        b = shapely.get_coordinates(shapely.line_interpolate_point(self.axis, np.minimum(s + 0.5, self.length)))
        run = (b - a) / np.maximum(np.linalg.norm(b - a, axis=1, keepdims=True), 1e-9)
        return run, np.column_stack([-run[:, 1], run[:, 0]])

    def _offset(self, point, s) -> float:
        centre = np.asarray(self.axis.interpolate(s).coords[0])
        _, normal = self._normal([s])
        return float((point - centre) @ normal[0])

    def point(self, s, offset) -> np.ndarray:
        """Survey points at stations and lateral offsets (left positive)."""
        s = np.atleast_1d(np.asarray(s, float))
        centre = shapely.get_coordinates(shapely.line_interpolate_point(self.axis, s))
        _, normal = self._normal(s)
        return centre + normal * np.broadcast_to(np.asarray(offset, float), len(s))[:, None]

    def edge(self, side: int, s) -> np.ndarray:
        """Lateral offset of the left (0) or right (1) edge at stations."""
        stations, offsets = self.lat[side]
        return np.interp(np.asarray(s, float), stations, offsets)

    def xz(self, points) -> np.ndarray:
        points = np.asarray(points, float).reshape(-1, 2)
        return np.column_stack([points[:, 0] - self.e0, self.n0 - points[:, 1]])

    def level(self, s) -> np.ndarray:
        s = np.asarray(s, float)
        return self.ends[0] + (self.ends[1] - self.ends[0]) * np.clip(s, 0.0, self.length) / max(self.length, 1e-9)

    def stations(self, a: float, b: float, step: float = STEP) -> np.ndarray:
        count = max(int(math.ceil((b - a) / step)), 1)
        return np.linspace(a, b, count + 1)

    def side_line(self, side: int, s, inset: float) -> np.ndarray:
        """Plan points (engine x, z) at stations, `inset` m inside the left or right edge."""
        offset = self.edge(side, s)
        offset = offset - np.sign(offset) * inset
        return self.xz(self.point(s, offset))


def supports(description: dict, frame: Frame, spec: dict) -> tuple[list[float], list[dict]]:
    """Stations of the supports from the first abutment's face to the last's (faces included) and the piers."""
    f0, f1 = description['faces']
    measured = sorted(s for s in description.get('piers', {}).get('measured', []) if f0 + 2 < s < f1 - 2)
    piers = [{'station': s, 'source': 'lidar'} for s in measured]
    fractions = (spec.get('piers') or {}).get('fractions')
    if fractions:
        wanted = [f0 + f * (f1 - f0) for f in fractions]
        snapped = []
        for station in wanted:
            near = [s for s in measured if abs(s - station) < 0.12 * (f1 - f0)]
            snapped.append({'station': min(near, key=lambda s: abs(s - station)) if near else station,
                            'source': 'lidar' if near else 'profile'})
        piers = snapped
    else:
        # The type's longest span completes what the scan did not see.
        stations = [f0, *measured, f1]
        added = []
        for a, b in zip(stations[:-1], stations[1:]):
            count = int(math.ceil((b - a) / spec['max_span'])) - 1
            added += [a + (b - a) * k / (count + 1) for k in range(1, count + 1)]
        piers += [{'station': s, 'source': 'type'} for s in added]
    piers.sort(key=lambda p: p['station'])
    return [f0, *[p['station'] for p in piers], f1], piers


def _ground(description: dict, s) -> np.ndarray:
    ground = np.asarray(description['ground']['heights'], float)
    step = description['ground']['step']
    return np.interp(np.asarray(s, float) / step, np.arange(len(ground)), ground)


def _depths(spec: dict, length: float, t: np.ndarray, use: str) -> np.ndarray:
    """Depth of the structure under the slab across a span (t from 0 to 1): haunched beams, arches, thin stays."""
    structure, body = spec['structure'], spec['body']
    if structure in ('cable-stayed', 'suspension'):
        return np.full_like(t, 0.25)
    if structure in ('arch', 'humpback'):
        middle, support = 0.35, max(1.0, length * 0.16)
    elif use == 'foot':
        middle, support = max(0.3, length / 40), max(0.5, length / 22)
    elif body == 'steel':
        middle, support = max(0.5, length / 35), max(0.8, length / 16)
    else:
        middle, support = max(0.5, length / 30), max(0.9, length / 14)
    if structure in ('arch', 'humpback'):
        # A circular segment: the soffit rises from the springing to the crown.
        x = 2 * t - 1
        return middle + (support - middle) * (1 - np.sqrt(np.clip(1 - x * x, 0, 1)))
    return middle + (support - middle) * (2 * t - 1) ** 2


def compile_bridge(description: dict) -> Output:
    frame = Frame(description)
    spec = style(description)
    mats = materials(spec)
    use = description.get('use', 'road')
    slab = float(spec.get('slab', decks.SLAB))
    ground_all = np.asarray(description['ground']['heights'], float)
    base = float(math.floor(min(WATER_LEVEL - BED_DEPTH - 1.0, float(np.nanmin(ground_all)) - ROOT - 1.0)))
    origin = (frame.e0 - ORIGIN_E, base, ORIGIN_N - frame.n0)
    solids = Solids(base, (origin[0], origin[2]))
    detail = Mesh()   # small pieces built with the object meshes (grilles, pylons), in the entity frame
    instances: list[Instances] = []
    key = description['id']
    f0, f1 = description['faces']
    water = description.get('water') or []
    banks = description.get('banks', [WATER_LEVEL + 1.0, WATER_LEVEL + 1.0])
    stops, piers = supports(description, frame, spec)
    support_mat, body_mat = mats[spec['support']], mats[spec['body']]

    def under_deck(s):
        return frame.level(s) - slab + TUCK

    def floor(s):
        """The lowest a girder may hang at stations: above the water, and a walking height above a bank under it."""
        s = np.asarray(s, float)
        out = np.full(s.shape, WATER_LEVEL + 0.8)
        wet = np.zeros(s.shape, bool)
        for a, b in water:
            wet |= (s >= a) & (s <= b)
        land = ~wet & (s > f0 - 0.5) & (s < f1 + 0.5)
        return np.where(land, np.maximum(out, _ground(description, s) + CLEAR_WALK), out)

    # Abutments: from each end of the deck to its face, under the slab, down into the bank.
    arches = [a for a in spec.get('arches', []) if a.get('end') in (0, 1)]
    for end, (a, b) in enumerate(((0.0, f0), (f1, frame.length))):
        if b - a < 0.5:
            continue
        s = frame.stations(a, b)
        bottom = float(np.nanmin(_ground(description, s))) - ROOT
        bottom = min(bottom, banks[end] - ROOT)
        cuts = []
        for arch in arches:
            if arch['end'] != end:
                continue
            centre = f0 - arch['from_face'] if end == 0 else f1 + arch['from_face']
            half = arch['width'] / 2
            if a + 0.5 < centre - half and centre + half < b - 0.5:
                cuts.append((centre - half, centre + half, arch))
        edges = sorted([a, b] + [x for c in cuts for x in c[:2]])
        for s0, s1 in zip(edges[:-1], edges[1:]):
            if s1 - s0 < 0.05:
                continue
            ss = frame.stations(s0, s1)
            arch = next((c[2] for c in cuts if abs(c[0] - s0) < 1e-6 and abs(c[1] - s1) < 1e-6), None)
            left, right = frame.side_line(0, ss, 0.0), frame.side_line(1, ss, 0.0)
            if arch is None:
                solids.band('abutment', support_mat, left, right, under_deck(ss), bottom)
                continue
            _arched_opening(solids, detail, frame, mats, support_mat, ss, arch, banks[end], bottom, under_deck, base)

    # Piers: a cutwater across the deck from below the bed to the girders.
    pier_rows = []
    for pier in piers:
        s = pier['station']
        span_left = [x for x in stops if x < s]
        span_right = [x for x in stops if x > s]
        if not span_left or not span_right:
            continue
        reach = max(s - span_left[-1], span_right[0] - s)
        top = float(under_deck(s) - _depths(spec, reach, np.array([0.0]), use)[0])
        on_water = any(a <= s <= b for a, b in water)
        bottom = (WATER_LEVEL - BED_DEPTH - 0.5) if on_water else float(_ground(description, s)) - ROOT
        if top - bottom < 1.0:
            continue
        thickness = (spec.get('piers') or {}).get('thickness', 2.2 if spec['support'] == 'stone' else 1.4)
        if spec['structure'] in ('cable-stayed', 'suspension'):
            thickness = 1.2
        left_off, right_off = float(frame.edge(0, s)), float(frame.edge(1, s))
        inset = 0.6
        a_pt = frame.xz(frame.point([s], [left_off - np.sign(left_off) * inset]))[0]
        b_pt = frame.xz(frame.point([s], [right_off - np.sign(right_off) * inset]))[0]
        footprint = shapely.LineString([a_pt, b_pt]).buffer(thickness / 2, quad_segs=6)
        ring = np.asarray(footprint.exterior.coords)[:-1]
        cap = shapely.LineString([a_pt, b_pt]).buffer(thickness / 2 + 0.25, quad_segs=6, cap_style='square', join_style='mitre')
        solids.prism('pier', support_mat, ring, top - 0.4, bottom)
        solids.prism('pier-cap', support_mat if spec['support'] == 'stone' else mats['concrete'],
                     np.asarray(cap.exterior.coords)[:-1], top + TUCK, top - 0.4)
        pier_rows.append({**pier, 'top': round(top, 2), 'bottom': round(bottom, 2)})

    # Spans: haunched girders, arches or a thin deck between the supports, across the deck's whole width: one solid
    # from face to face, deepest over the supports.
    pieces, depths = [], []
    for a, b in zip(stops[:-1], stops[1:]):
        if b - a < 0.5:
            continue
        ss = frame.stations(a, b)
        pieces.append(ss)
        depths.append(_depths(spec, b - a, (ss - a) / (b - a), use))
    if pieces:
        ss = np.concatenate(pieces)
        depth = np.concatenate(depths)
        ss = np.unique(np.round(ss, 6))
        depth = np.array([depth[np.isclose(np.concatenate(pieces), s)].max() for s in ss])
        top = under_deck(ss)
        bottom = np.minimum(np.maximum(top - depth, floor(ss)), top - 0.3)
        left, right = frame.side_line(0, ss, 0.12), frame.side_line(1, ss, 0.12)
        solids.band('span', body_mat, left, right, top, bottom)

    # Edge beams along both sides of the whole deck: the parapets stand on them.
    ss = frame.stations(0.0, frame.length)
    edge_mat = mats['stone'] if spec['parapets'].get('approach') in ('balustrade', 'solid') and spec['support'] == 'stone' and spec['body'] == 'stone' else \
        (mats['steel'] if spec['body'] == 'steel' else mats['concrete'])
    for side in (0, 1):
        outer, inner = frame.side_line(side, ss, -0.04), frame.side_line(side, ss, EDGE)
        level = frame.level(ss)
        solids.band('edge-beam', edge_mat, outer, inner, level + EDGE_UP, level - slab - EDGE_DOWN)

    # Parapets: by zone (over the abutments, over the spans), where the ground beside the deck falls away.
    side_ground = _ground(description, ss)
    standing = frame.level(ss) - side_ground > PARAPET_DROP
    zones = [('approach', 0.0, f0), ('span', f0, f1), ('approach', f1, frame.length)]
    for zone, a, b in zones:
        kind = spec['parapets'].get(zone, 'railing')
        if b - a < 0.5:
            continue
        for run_a, run_b in _runs(ss, standing, a, b):
            for side in (0, 1):
                _parapet(solids, instances, frame, mats, spec, kind, side, run_a, run_b, base, f'{key}/{zone}-{side}-{run_a:.0f}')

    # End pylons where the spans meet the abutments.
    pylons = spec.get('pylons')
    if pylons:
        for station in (f0, f1):
            for side in (0, 1):
                offset = float(frame.edge(side, station))
                offset -= np.sign(offset) * pylons.get('width', 1.1) / 2
                at = frame.xz(frame.point([station], [offset]))[0]
                run, _ = frame._normal([station])
                _pylon(detail, mats, at, float(frame.level(station)) + EDGE_UP, pylons, run[0], base)

    # Gates mapped under a deck's end: a grille closes the bank's passage under it on both sides.
    for end in (0, 1):
        near = [g for g in description.get('gates', []) if (g['station'] < frame.length / 2) == (end == 0)]
        if not near or not water:
            continue
        a, b = (f0, water[0][0]) if end == 0 else (water[-1][1], f1)
        if b - a < 0.8:
            continue
        sill = banks[end]
        for side in (0, 1):
            ss_g = frame.stations(a, b, 0.5)
            line = frame.side_line(side, ss_g, 0.3)
            top = float(np.min(np.minimum(under_deck(ss_g), floor(ss_g) + 0.2)))
            _grille_run(detail, mats['rail'], line, sill, min(top, sill + 3.2), base)

    parts = solids.parts() + [p for p in detail.parts()]
    return Output(origin, parts, instances=instances, error_m={0: 0.05})


def _runs(stations, mask, a, b) -> list[tuple[float, float]]:
    """Station ranges within [a, b] where the mask holds."""
    inside = (stations >= a - 1e-6) & (stations <= b + 1e-6) & mask
    runs, start = [], None
    for s, on in zip(stations, inside):
        if on and start is None:
            start = s
        if not on and start is not None:
            runs.append((start, prev))
            start = None
        prev = s
    if start is not None:
        runs.append((start, prev))
    return [(max(x, a), min(y, b)) for x, y in runs if min(y, b) - max(x, a) > 1.0]


def _parapet(solids, instances, frame, mats, spec, kind, side, a, b, base, key):
    ss = frame.stations(a, b)
    level = frame.level(ss) + EDGE_UP
    if kind == 'solid':
        outer, inner = frame.side_line(side, ss, 0.05), frame.side_line(side, ss, 0.4)
        solids.band('parapet', mats['stone'], outer, inner, level + 0.92, level - 0.05)
        outer, inner = frame.side_line(side, ss, 0.0), frame.side_line(side, ss, 0.46)
        solids.band('parapet-coping', mats['pylon'], outer, inner, level + 1.05, level + 0.92)
        return
    if kind == 'balustrade':
        outer, inner = frame.side_line(side, ss, 0.04), frame.side_line(side, ss, 0.44)
        solids.band('balustrade-plinth', mats['cast'], outer, inner, level + 0.22, level - 0.05)
        outer, inner = frame.side_line(side, ss, 0.0), frame.side_line(side, ss, 0.48)
        solids.band('balustrade-coping', mats['cast'], outer, inner, level + 1.02, level + 0.88)
        # Dies: solid blocks at the ends and every few metres, balusters between them.
        count = max(int(round((b - a) / DIE_PITCH)), 1)
        dies = np.linspace(a, b, count + 1)
        for s in dies:
            dd = np.array([max(s - 0.22, a), min(s + 0.22, b)])
            if dd[1] - dd[0] < 0.1:
                continue
            lv = frame.level(dd) + EDGE_UP
            outer, inner = frame.side_line(side, dd, 0.02), frame.side_line(side, dd, 0.46)
            solids.band('balustrade-die', mats['cast'], outer, inner, lv + 0.88, lv + 0.22)
        rows = []
        for s0, s1 in zip(dies[:-1], dies[1:]):
            n = int((s1 - s0 - 0.44) // BALUSTER_PITCH)
            if n < 1:
                continue
            for s in s0 + 0.22 + (np.arange(n) + 0.5) * ((s1 - s0 - 0.44) / n):
                p = frame.side_line(side, [s], 0.24)[0]
                run = frame.side_line(side, [min(s + 0.2, b)], 0.24)[0] - frame.side_line(side, [max(s - 0.2, a)], 0.24)[0]
                yaw = yaw_of_run(math.degrees(math.atan2(run[0], -run[1])))
                rows.append([p[0], float(frame.level(s)) + EDGE_UP + 0.22 - base, p[1], yaw, 1.0, 1.0, 1.0])
        if rows:
            instances.append(Instances(_baluster(mats['cast']), np.round(np.array(rows), 4), ids=[f'{key}/baluster-{k}' for k in range(len(rows))]))
        return
    # Railings: posts at the joints, panels between them (open bars, or the heritage lattice).
    height = spec['rail_height']
    count = max(int(round((b - a) / PANEL)), 1)
    joints = np.linspace(a, b, count + 1)
    points = frame.side_line(side, joints, 0.22)
    levels = frame.level(joints) + EDGE_UP - base
    panels, posts = [], []
    for k in range(count):
        p, q = points[k], points[k + 1]
        run = q - p
        length = float(np.hypot(*run))
        if length < 0.3:
            continue
        yaw = yaw_of_run(math.degrees(math.atan2(run[0], -run[1])))
        middle = (p + q) / 2
        panels.append([middle[0], (levels[k] + levels[k + 1]) / 2, middle[1], yaw, length / PANEL, 1.0, 1.0])
        posts.append([p[0], levels[k], p[1], yaw, 1.0, 1.0, 1.0])
    if len(points):
        posts.append([points[-1][0], levels[-1], points[-1][1], posts[-1][3] if posts else 0.0, 1.0, 1.0, 1.0])
    lattice = kind == 'lattice'
    if panels:
        instances.append(Instances(_rail_panel(mats['rail'], height, lattice), np.round(np.array(panels), 4),
                                   ids=[f'{key}/panel-{k}' for k in range(len(panels))], behaviour='fixed',
                                   collision={'shape': 'box', 'size': [PANEL, height, 0.08]}))
    if posts:
        instances.append(Instances(_rail_post(mats['rail'], height, lattice), np.round(np.array(posts), 4),
                                   ids=[f'{key}/post-{k}' for k in range(len(posts))], behaviour='fixed',
                                   collision={'shape': 'box', 'size': [0.12, height, 0.12]}))


def _baluster(material: Material) -> list:
    mesh = Mesh()
    mesh.lathe('baluster', material, [(0.075, 0.0), (0.075, 0.06), (0.05, 0.1), (0.06, 0.2), (0.085, 0.34), (0.06, 0.48),
                                      (0.045, 0.56), (0.06, 0.6), (0.075, 0.6), (0.075, 0.66)], sides=8)
    return mesh.parts()


def _rail_panel(material: Material, height: float, lattice: bool) -> list:
    """A 2 m railing panel along +x about its middle: top and bottom rails, vertical bars or a diamond lattice."""
    mesh = Mesh()
    half = PANEL / 2
    mesh.box('rail', material, (0.0, height - 0.03, 0.0), (PANEL, 0.06, 0.07))
    mesh.box('rail', material, (0.0, 0.12, 0.0), (PANEL, 0.05, 0.05))
    if lattice:
        mesh.box('rail', material, (0.0, height - 0.22, 0.0), (PANEL, 0.04, 0.04))
        low, high = 0.15, height - 0.24
        pitch = 0.25
        for x in np.arange(-half - (high - low), half, pitch):
            for direction in (1.0, -1.0):
                start = np.array([x, low])
                end = start + np.array([high - low, high - low])
                if direction < 0:
                    start, end = np.array([x + (high - low), low]), np.array([x, high])
                seg = shapely.LineString([start, end]).intersection(shapely.box(-half, low, half, high))
                if seg.is_empty or seg.length < 0.05:
                    continue
                (x0, y0), (x1, y1) = seg.coords[0], seg.coords[-1]
                mesh.beam('rail', material, (x0, y0, 0.0), (x1, y1, 0.0), 0.02, 0.02, up=(0.0, 0.0, 1.0))
        for x in np.arange(-half + pitch, half, pitch * 2):
            mesh.box('rail', material, (x, height - 0.12, 0.0), (0.02, 0.16, 0.02))
    else:
        for x in np.arange(-half + 0.06, half, 0.12):
            mesh.box('rail', material, (x, (0.14 + height - 0.06) / 2, 0.0), (0.018, height - 0.2, 0.018))
    return mesh.parts()


def _rail_post(material: Material, height: float, lattice: bool) -> list:
    mesh = Mesh()
    size = 0.09 if lattice else 0.07
    mesh.box('post', material, (0.0, height / 2, 0.0), (size, height, size))
    if lattice:
        mesh.box('post', material, (0.0, height + 0.03, 0.0), (0.12, 0.06, 0.12))
    return mesh.parts()


def _pylon(mesh: Mesh, mats: dict, at, floor_y: float, spec: dict, run, base: float):
    """A tapered granite obelisk on a stepped plinth with a bronze collar and lantern (the old `bridge_kit.entrances`)."""
    width = spec.get('width', 1.1)
    height = spec.get('height', 4.8)
    yaw = yaw_of_run(math.degrees(math.atan2(run[0], -run[1])))
    levels = [(0.0, 1.22), (0.19, 1.22), (0.22, 1.06), (0.95, 1.06), (1.02, 0.92), (1.12, 0.92), (1.14, 0.77),
              (height - 0.22, 0.49), (height - 0.09, 0.49), (height, 0.31)]
    y0 = floor_y - base
    for (h0, w0), (h1, w1) in zip(levels[:-1], levels[1:]):
        if h1 - h0 < 1e-3:
            continue
        _frustum(mesh, 'pylon', mats['pylon'], at, y0 + h0, y0 + h1, w0 * width, w1 * width, yaw)
    collar = height - 0.85
    for lift, size in ((collar, 0.64), (collar + 0.08, 0.72)):
        _frustum(mesh, 'pylon-bronze', mats['bronze'], at, y0 + lift, y0 + lift + 0.045, size * width, size * width, yaw)
    if spec.get('lantern'):
        top = y0 + height
        mesh.lathe('pylon-bronze', mats['bronze'], [(0.0, top), (0.12, top), (0.08, top + 0.25), (0.22, top + 0.45), (0.24, top + 0.75),
                                                   (0.1, top + 0.9), (0.03, top + 1.1), (0.0, top + 1.12)], sides=8, centre=(at[0], at[1]))


def _frustum(mesh: Mesh, part: str, material: Material, at, y0: float, y1: float, w0: float, w1: float, yaw: float):
    corners = []
    for sx in (-1, 1):
        for sy, w, y in ((-1, w0, y0), (1, w1, y1)):
            for sz in (-1, 1):
                corners.append([sx * w / 2, y, sz * w / 2])
    corners = np.asarray(corners, float)
    c, s = math.cos(yaw), math.sin(yaw)
    turned = corners @ np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]).T
    turned[:, 0] += at[0]
    turned[:, 2] += at[1]
    mesh.hull8(part, material, turned)


def _grille_run(mesh: Mesh, material: Material, line, sill: float, top: float, base: float):
    """A grille of vertical bars and two rails along a plan line, from the sill to `top`."""
    line = np.asarray(line, float)
    length = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
    if length[-1] < 0.3 or top - sill < 0.5:
        return
    y0, y1 = sill - base, top - base
    for u in np.arange(0.06, length[-1], 0.14):
        x, z = np.interp(u, length, line[:, 0]), np.interp(u, length, line[:, 1])
        mesh.box('grille', material, (x, (y0 + y1) / 2, z), (0.025, y1 - y0, 0.025))
    for y in (y0 + 0.1, y1 - 0.1, (y0 + y1) / 2):
        for k in range(len(line) - 1):
            mesh.beam('grille', material, (line[k][0], y, line[k][1]), (line[k + 1][0], y, line[k + 1][1]), 0.05, 0.05)


def _arched_opening(solids, mesh, frame, mats, material, ss, arch, sill, bottom, under_deck, base):
    """An arched, grilled opening through both side faces of an abutment: a vaulted niche a metre deep on each side,
    the abutment solid between them, the sill below."""
    depth = 1.0
    s0, s1 = float(ss[0]), float(ss[-1])
    width = s1 - s0
    height = arch['height']
    rise = min(width / 2, height * 0.6)
    spring = sill + height - rise
    centre = (s0 + s1) / 2
    x = np.clip((ss - centre) / max(width / 2, 1e-9), -1, 1)
    crown = spring + rise * np.sqrt(np.clip(1 - x * x, 0, 1))
    top = under_deck(ss)
    vault = np.minimum(crown, top - 0.3)
    # The faces the opening is in: those turned towards `facing` (a compass bearing), else both.
    _, normal = frame._normal([centre])
    left_bearing = math.degrees(math.atan2(normal[0][0], normal[0][1]))
    sides = [side for side, bearing in ((0, left_bearing), (1, left_bearing + 180.0))
             if 'facing' not in arch or abs((bearing - arch['facing'] + 180.0) % 360.0 - 180.0) <= 60.0]
    lines = [(frame.side_line(side, ss, 0.0), frame.side_line(side, ss, depth)) for side in (0, 1)]
    # The dark core between the niches (its sides are their backs), the vaults over them, the sill under them; a face
    # without an opening stays whole.
    solids.band('abutment-void', mats['void'], lines[0][1], lines[1][1], top, bottom)
    for side in (0, 1):
        outer, inner = lines[side]
        if side not in sides:
            solids.band('abutment', material, outer, inner, top, bottom)
            continue
        solids.band('abutment', material, outer, inner, top, vault)
        solids.band('abutment', material, outer, inner, np.full(len(ss), sill), bottom)
        if arch.get('grille'):
            grille = inner + (outer - inner) * 0.85
            lu = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(grille, axis=0), axis=1))])
            shape = shapely.Polygon(np.vstack([np.column_stack([lu, np.full(len(ss), sill)]), np.column_stack([lu[::-1], vault[::-1]])]))
            for k in np.arange(-2 * width, 2 * width, 0.28):
                for sign in (1.0, -1.0):
                    a = np.array([k, sill]) if sign > 0 else np.array([k + height, sill])
                    b = a + np.array([height * sign, height])
                    seg = shapely.LineString([a, b]).intersection(shape)
                    for piece in shapely.get_parts(seg):
                        if piece.geom_type != 'LineString' or piece.length < 0.05:
                            continue
                        (u0, v0), (u1, v1) = piece.coords[0], piece.coords[-1]
                        p0 = np.array([np.interp(u0, lu, grille[:, 0]), v0 - base, np.interp(u0, lu, grille[:, 1])])
                        p1 = np.array([np.interp(u1, lu, grille[:, 0]), v1 - base, np.interp(u1, lu, grille[:, 1])])
                        mesh.beam('grille', mats['rail'], p0, p1, 0.025, 0.025)
            # The arch ring of the grille.
            ring = np.column_stack([grille[:, 0], vault - base, grille[:, 1]])
            for k in range(len(ring) - 1):
                mesh.beam('grille', mats['rail'], ring[k], ring[k + 1], 0.04, 0.04)
