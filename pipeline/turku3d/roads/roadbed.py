"""The drivable roadbed under a tile's car lanes, measured on its finished collision.

Three city-wide rules, each driven only by the street graph and the collision triangles:

1. **Lane heights on the floor** (``repair_heights``). A car edge's surveyed height that leaves its
   neighbours by more than ``SPIKE`` (the roof or the soffit of a building passage, picked where its
   floor had a gap) takes the floor nearest the neighbours' level, else their interpolation.
2. **No pits under a lane** (``pits``). Along wheel tracks across each carriageway, a gap in the
   floor or a trench deeper than ``PIT_DEPTH`` and at most ``PIT_MAX`` long (a deck-to-land seam,
   an inter-deck joint, a sliver at a passage's wall line) is bridged by a flat collision slab at the
   level of the floor on either side.
3. **No steps on structures** (``steps``). On bridge and tunnel edges and the edges that meet them,
   a 4-30 cm step across a track gets a 1:16 ramp slab on its low side.
4. **A deck carries its lanes** (``_deck_width``). On a bridge edge, tracks inside the lanes'
   envelope without deck under them (a deck built narrower than its lanes, a wedge between two
   decks of one crossing) get the deck's level carried out from the track inside them.

``slabs`` returns the slabs as oriented boxes (world-local metres) with per-rule counts. The tile
migration ``roadbed@1`` (``turku3d.migrations.roadbed``) adds them to the collision cells and writes
the repaired heights; the generator gets the same through the migration chain it runs at the end of
every build.
"""

from __future__ import annotations

import math

import numpy as np
import shapely

CAR = 2  # edge mode bit (roads.json bits.car)
SPIKE = 1.0  # m off the median of the edge's heights within WINDOW: not the lane's own level
WINDOW = 6.0  # m each side of a vertex for that median
FLOOR_REACH = 1.2  # m around the lane height where a floor counts as the lane's own
DS = 0.25  # m between samples along a track
TRACK = 0.5  # m between tracks across the carriageway
PIT_DEPTH = 0.05  # m below the chord between the floor on either side
PIT_MAX = 3.0  # m: longer gaps are openings, not pits
PIT_GRADE = 0.12  # the chord across a pit is at most this steep (else it is a step)
STEP_MIN, STEP_MAX = 0.04, 0.30  # m: steps the structure rule ramps
STEP_GRADE = 1 / 16
SLAB = 0.3  # m slab thickness
STRUCTURE_FLAGS = ("BRIDGE", "TUNNEL")
EDGE_MARGIN = 1.0  # m beyond the mapped half width
END_REACH = 6.0  # m: tracks continue past an edge's ends, through its junction


CARRIAGEWAY = ("road", "crossing", "bridge_deck", "cobblestone", "paving_slab", "ramp")


class Floor:
    """The upward collision faces of a tile (world-local triangles) with their material ids."""

    def __init__(self, triangles, materials=None):
        triangles = np.asarray(triangles, np.float64).reshape(-1, 3, 3)
        materials = np.zeros(len(triangles), np.int64) if materials is None else np.asarray(materials)
        normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        keep = normal[:, 1] > 0.25 * np.linalg.norm(normal, axis=1)
        self.triangles, self.materials = triangles[keep], materials[keep]
        self.tree = shapely.STRtree(shapely.polygons(self.triangles[:, :, [0, 2]]))

    def candidates(self, points, with_materials=False):
        """(point index, height[, material]) of every floor over each of ``points`` (N, 2)."""
        points = np.asarray(points, np.float64).reshape(-1, 2)
        if not len(points) or not len(self.triangles):
            empty = (np.zeros(0, int), np.zeros(0))
            return (*empty, np.zeros(0, int)) if with_materials else empty
        q, t = self.tree.query(shapely.points(points), predicate="intersects")
        tri = self.triangles[t]
        a = tri[:, 0]
        b, c = tri[:, 1] - a, tri[:, 2] - a
        d = points[q] - a[:, [0, 2]]
        det = b[:, 0] * c[:, 2] - b[:, 2] * c[:, 0]
        ok = np.abs(det) > 1e-12
        det = np.where(ok, det, 1.0)
        u = (d[:, 0] * c[:, 2] - d[:, 1] * c[:, 0]) / det
        v = (b[:, 0] * d[:, 1] - b[:, 2] * d[:, 0]) / det
        y = (a[:, 1] + u * b[:, 1] + v * c[:, 1])[ok]
        return (q[ok], y, self.materials[t][ok]) if with_materials else (q[ok], y)

    def top(self, points, reference, reach=FLOOR_REACH):
        """Per point the highest floor within ``reach`` of ``reference`` (what a wheel rests on) and
        its material; NaN and -1 where there is none."""
        reference = np.asarray(reference, np.float64)
        out, mats = np.full(len(reference), np.nan), np.full(len(reference), -1)
        q, y, mat = self.candidates(points, with_materials=True)
        near = np.abs(y - reference[q]) <= reach
        q, y, mat = q[near], y[near], mat[near]
        if not len(q):
            return out, mats
        order = np.lexsort((-y, q))
        q, y, mat = q[order], y[order], mat[order]
        first = np.r_[True, q[1:] != q[:-1]]
        out[q[first]], mats[q[first]] = y[first], mat[first]
        return out, mats

    def nearest(self, points, reference, reach=FLOOR_REACH):
        """Per point the floor nearest ``reference`` within ``reach``; NaN where there is none."""
        reference = np.asarray(reference, np.float64)
        out = np.full(len(reference), np.nan)
        q, y = self.candidates(points)
        if not len(q):
            return out
        gap = np.abs(y - reference[q])
        near = gap <= reach
        q, y, gap = q[near], y[near], gap[near]
        if not len(q):
            return out
        order = np.lexsort((gap, q))
        q, y = q[order], y[order]
        first = np.r_[True, q[1:] != q[:-1]]
        out[q[first]] = y[first]
        return out


def car_edges(graph):
    """(edge index, polyline (n, 2), heights (n,), attrs dict, flags) of every car edge with heights."""
    nodes = np.asarray(graph["nodes"], np.float64).reshape(-1, 2)
    columns = graph["edgeAttrColumns"]
    flag_bits = graph["bits"]["edge_flags"]
    heights = graph.get("edgeHeights") or [None] * len(graph["edges"])
    for k, (edge, ys) in enumerate(zip(graph["edges"], heights, strict=True)):
        if not edge[2] & CAR or ys is None:
            continue
        line = np.vstack([nodes[edge[0]], np.asarray(edge[4:], np.float64).reshape(-1, 2), nodes[edge[1]]])
        if len(ys) != len(line):
            continue
        attrs = dict(zip(columns, graph["edgeAttrs"][k], strict=True))
        flags = {name for name, bit in flag_bits.items() if attrs["flags"] & bit}
        yield k, line, np.asarray(ys, np.float64), attrs, flags


def stations(line):
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))]


def repair_heights(graph, floor, stats):
    """Rule 1, in place on ``graph['edgeHeights']``."""
    heights = graph.get("edgeHeights")
    for k, line, ys, _, _ in car_edges(graph):
        s = stations(line)
        reference = np.array([np.median(ys[np.abs(s - si) <= WINDOW]) for si in s])
        spike = np.abs(ys - reference) > SPIKE
        if not spike.any() or spike.all():
            continue
        good = ~spike
        level = np.interp(s, s[good], ys[good])
        found = floor.nearest(line[spike], level[spike], SPIKE)
        fixed = ys.copy()
        fixed[spike] = np.where(np.isfinite(found), found, level[spike])
        heights[k] = np.round(fixed, 3).tolist()
        stats["lane_height_edges"] += 1
        stats["lane_height_vertices"] += int(spike.sum())


def half_width(attrs):
    """Half the swept carriageway: the mapped width and a margin, since lanes keep their offsets
    through the junctions at an edge's ends. Samples off the carriageway's materials never rim a pit."""
    return float(attrs["width"]) / 2 + EDGE_MARGIN


def _quaternion(x_axis, y_axis, z_axis):
    m = np.c_[x_axis, y_axis, z_axis]
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        w = math.sqrt(1 + trace) * 2
        q = ((m[2, 1] - m[1, 2]) / w, (m[0, 2] - m[2, 0]) / w, (m[1, 0] - m[0, 1]) / w, w / 4)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        w = math.sqrt(1 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = (w / 4, (m[0, 1] + m[1, 0]) / w, (m[0, 2] + m[2, 0]) / w, (m[2, 1] - m[1, 2]) / w)
    elif m[1, 1] > m[2, 2]:
        w = math.sqrt(1 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = ((m[0, 1] + m[1, 0]) / w, w / 4, (m[1, 2] + m[2, 1]) / w, (m[0, 2] - m[2, 0]) / w)
    else:
        w = math.sqrt(1 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = ((m[0, 2] + m[2, 0]) / w, (m[1, 2] + m[2, 1]) / w, w / 4, (m[1, 0] - m[0, 1]) / w)
    q = np.asarray(q)
    return tuple(float(v) for v in q / np.linalg.norm(q))


def slab(a, b, width):
    """An oriented box whose top face runs from ``a`` to ``b`` (world x, y, z), ``width`` across.

    Returns (centre, half extents, quaternion x, y, z, w)."""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    along = b - a
    length = float(np.linalg.norm(along))
    x_axis = along / length
    z_axis = np.array([-x_axis[2], 0.0, x_axis[0]])
    z_axis /= np.linalg.norm(z_axis)
    y_axis = np.cross(z_axis, x_axis)
    centre = (a + b) / 2 - y_axis * SLAB / 2
    return (tuple(round(float(v), 4) for v in centre),
            (round(length / 2 + 0.05, 4), SLAB / 2, round(width / 2, 4)),
            _quaternion(x_axis, y_axis, z_axis))


def _tracks(line, ys, attrs, bbox):
    """Samples on the tracks across one edge: positions (T, S, 2), lane level (S,), tangents (S, 2)."""
    s = stations(line)
    if s[-1] < DS * 4:
        return None
    along = np.arange(-END_REACH, s[-1] + END_REACH + 1e-9, DS)
    inner = np.clip(along, 0, s[-1])
    centre = np.c_[np.interp(inner, s, line[:, 0]), np.interp(inner, s, line[:, 1])]
    head = (line[1] - line[0]) / max(np.linalg.norm(line[1] - line[0]), 1e-9)
    tail = (line[-1] - line[-2]) / max(np.linalg.norm(line[-1] - line[-2]), 1e-9)
    centre += np.minimum(along, 0)[:, None] * head + np.maximum(along - s[-1], 0)[:, None] * tail
    level = np.interp(inner, s, ys)
    tangent = np.gradient(centre, axis=0)
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1, keepdims=True), 1e-9)
    normal = np.c_[-tangent[:, 1], tangent[:, 0]]
    hw = half_width(attrs)
    offsets = np.arange(-hw, hw + 1e-9, TRACK) if hw > TRACK else np.zeros(1)
    offsets = offsets - (offsets[0] + offsets[-1]) / 2
    points = centre[None] + offsets[:, None, None] * normal[None]
    x0, z0, x1, z1 = bbox
    inside = (points[..., 0] > x0) & (points[..., 0] < x1) & (points[..., 1] > z0) & (points[..., 1] < z1)
    on_edge = (along >= 0) & (along <= s[-1])
    return points, level, tangent, inside, offsets, on_edge


def _pit_runs(h):
    """(i, j) index pairs: floor samples i and j, at most PIT_MAX apart, with only gaps or floor
    deeper than PIT_DEPTH below their chord between them."""
    runs, i, n = [], 0, len(h)
    limit = int(round(PIT_MAX / DS)) + 1
    while i < n - 2:
        if not np.isfinite(h[i]):
            i += 1
            continue
        j = None
        for k in range(i + 1, min(n, i + limit + 1)):
            if np.isfinite(h[k]) and h[k] >= h[i] - PIT_DEPTH - PIT_GRADE * (k - i) * DS:
                j = k
                break
        if j is None or j == i + 1:
            i += 1
            continue
        chord = h[i] + (h[j] - h[i]) * (np.arange(i + 1, j) - i) / (j - i)
        inner = h[i + 1 : j]
        if (abs(h[j] - h[i]) <= PIT_GRADE * (j - i) * DS + PIT_DEPTH
                and (~np.isfinite(inner) | (inner < chord - PIT_DEPTH)).all()):
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _step_runs(h):
    """(k, rise): a step between samples k and k + 1 beyond the grade on either side (the
    strongest sample of each step)."""
    n = len(h)
    excess = np.zeros(n)
    for k in range(2, n - 3):
        window = h[k - 2 : k + 4]
        if np.isfinite(window).all():
            before, after = (h[k] - h[k - 2]) / 2, (h[k + 3] - h[k + 1]) / 2
            excess[k] = (h[k + 1] - h[k]) - (before + after) / 2
    out = []
    for k in range(2, n - 3):
        e = abs(excess[k])
        if STEP_MIN <= e <= STEP_MAX and e >= np.abs(excess[max(0, k - 3) : k + 4]).max():
            out.append((k, float(h[k + 1] - h[k])))
    return out


LEDGE = 0.03  # m: a slab's corners at its ends sit on the floor within this


def _flush(floor, a, b, width):
    """Whether a slab from ``a`` to ``b`` meets the floor at all four end corners: a slab across an
    oblique seam or a cross slope would stand proud of the lower side as a ledge."""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    along = (b - a)[[0, 2]]
    norm = np.linalg.norm(along)
    if norm < 1e-6:
        return False
    side = np.array([-along[1], along[0]]) / norm * (width / 2)
    points = np.array([a[[0, 2]] + side, a[[0, 2]] - side, b[[0, 2]] + side, b[[0, 2]] - side])
    expected = np.array([a[1], a[1], b[1], b[1]])
    flush, _ = floor.top(points, expected, LEDGE)
    # A corner over a gap or a deep trench is part of the pit; one over a surface a few
    # centimetres lower (an oblique seam, a cross slope) would leave the slab standing proud.
    below, _ = floor.top(points, expected - (LEDGE + STEP_MAX) / 2, (STEP_MAX - LEDGE) / 2)
    return bool((np.isfinite(flush) | ~np.isfinite(below)).all())


def _plateau(h):
    """Samples of one continuous surface: finite, and within STEP_MIN of their own straight fit."""
    if len(h) < 2 or not np.isfinite(h).all():
        return False
    x = np.arange(len(h))
    fit = np.polyval(np.polyfit(x, h, 1), x)
    return bool(np.abs(h - fit).max() <= STEP_MIN)


def slabs(graph, floor, bbox, stats):
    """Rules 2 and 3: the slabs for the tile's car edges, as (centre, half, rotation, rule)."""
    from turku3d.col.materials import mat_id

    carriageway = [mat_id(name) for name in CARRIAGEWAY]
    out, seen = [], set()
    structure_nodes = set()
    for k, _, _, _, flags in car_edges(graph):
        if flags & set(STRUCTURE_FLAGS):
            edge = graph["edges"][k]
            structure_nodes.update((edge[0], edge[1]))

    def add(a, b, width, rule):
        key = (rule, *np.round(np.r_[a, b] / 0.1).astype(int).tolist())
        if key in seen:
            return
        if not _flush(floor, a, b, width):
            stats[f"{rule}_ledges_skipped"] += 1
            return
        seen.add(key)
        centre, half, rotation = slab(a, b, width)
        out.append((centre, half, rotation, rule))
        stats[f"{rule}_slabs"] += 1

    for k, line, ys, attrs, flags in car_edges(graph):
        sampled = _tracks(line, ys, attrs, bbox)
        if sampled is None:
            continue
        points, level, tangent, inside, offsets, on_edge = sampled
        flat = points.reshape(-1, 2)
        h, mat = floor.top(flat, np.tile(level, len(points)))
        h, mat = h.reshape(points.shape[:2]), mat.reshape(points.shape[:2])
        h[~inside] = np.nan
        paved = np.isin(mat, carriageway)
        edge = graph["edges"][k]
        structure = bool(flags & set(STRUCTURE_FLAGS)) or edge[0] in structure_nodes \
            or edge[1] in structure_nodes
        for t in range(len(points)):
            track = h[t]
            for i, j in _pit_runs(track):
                if not (inside[t, i] and inside[t, j] and paved[t, i] and paved[t, j]):
                    continue
                a = (points[t, i, 0], track[i], points[t, i, 1])
                b = (points[t, j, 0], track[j], points[t, j, 1])
                add(a, b, TRACK + 0.1, "pit")
                track[i + 1 : j] = track[i] + (track[j] - track[i]) * (np.arange(i + 1, j) - i) / (j - i)
            if not structure:
                continue
            for i, rise in _step_runs(track):
                length = abs(rise) / STEP_GRADE
                n = int(math.ceil(length / DS))
                if rise < 0:  # drop going forward: the ramp runs down on the far side
                    top, foot = i, min(len(track) - 1, i + 1 + n)
                else:  # rise going forward: the ramp runs up on the near side
                    top, foot = i + 1, max(0, i - n)
                if not (np.isfinite(track[foot]) and inside[t, foot] and inside[t, top]
                        and paved[t, top] and paved[t, foot] and paved[t, i] and paved[t, i + 1]):
                    continue
                low = track[min(i + 1, foot) : max(i + 1, foot) + 1] if rise < 0 \
                    else track[min(foot, i) : max(foot, i) + 1]
                if not _plateau(low):
                    continue  # the low side is a hole or another step, not a surface to ramp onto
                a = (points[t, top, 0], track[top], points[t, top, 1])
                b = (points[t, foot, 0], track[foot], points[t, foot, 1])
                add(a, b, TRACK + 0.1, "step")
        if "BRIDGE" in flags:
            for a, b in _deck_width(h, paved, inside & on_edge[None], points, offsets, attrs):
                add(a, b, TRACK + 0.1, "deck")
    return out


DECK_PIECE = 16  # samples (4 m) per deck-width slab, so a chord follows the deck's camber


def _deck_width(h, paved, inside, points, offsets, attrs):
    """Rule 4: a bridge deck carries its lanes' full width. Outward from the centre track, a track
    inside the lanes' envelope (1.75 m per lane each side of the centre plus a car's half width)
    that has no deck under it, or only something lower, gets the deck's level carried out from the
    track inside it. A raised kerb or footway (not carriageway) stops the carry."""
    lanes = max(1, int(attrs.get("lanes_fwd", 0)) + int(attrs.get("lanes_bwd", 0)))
    reach = min(1.75 * lanes + 0.9, float(attrs["width"]) / 2 + EDGE_MARGIN)
    centre = int(np.argmin(np.abs(offsets)))
    out = []
    for step in (1, -1):
        ref = np.where(paved[centre] & inside[centre], h[centre], np.nan)
        t = centre + step
        while 0 <= t < len(offsets) and abs(offsets[t]) <= reach:
            low = (~np.isfinite(h[t]) | (h[t] < ref - PIT_DEPTH)) & np.isfinite(ref) & inside[t]
            for run in np.split(np.flatnonzero(low), np.flatnonzero(np.diff(np.flatnonzero(low)) > 1) + 1):
                for piece in range(0, len(run), DECK_PIECE):
                    part = run[piece : piece + DECK_PIECE + 1]
                    i, j = int(part[0]), int(part[-1])
                    if j == i:
                        continue
                    out.append(((points[t, i, 0], ref[i], points[t, i, 1]),
                                (points[t, j, 0], ref[j], points[t, j, 1])))
            ref = np.where(low, ref, np.where(np.isfinite(h[t]) & paved[t], h[t], np.nan))
            t += step
    return out
